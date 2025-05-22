import xml.etree.ElementTree as ET
from xml.dom import minidom
from collections import deque
import os
import traceback
import hashlib
import tempfile


class BPMNData:
    def __init__(self, process_id=None, process_name=None):
        self.process_id = process_id
        self.process_name = process_name
        self.elements = {}
        self.sequence_flows = {}

    def add_element(self, element_data):
        if 'id' not in element_data:
            return

        element_data.setdefault('bpmn_markings', [])
        element_data.setdefault('_inclusive_path_origin_flow_id', None)
        self.elements[element_data['id']] = element_data

    def add_bpmn_marking(self, element_id: str, marking_type: str, associated_gateway_id: str):
        element = self.get_element(element_id)
        if not element:
            return

        if 'bpmn_markings' not in element:
            element['bpmn_markings'] = []

        valid_marking_types = {"S+", "S-", "J+", "J-"}
        if marking_type not in valid_marking_types:
            return

        if not associated_gateway_id:
            return

        for existing_marking in element['bpmn_markings']:
            if existing_marking['type'] == marking_type and existing_marking['gateway_id'] == associated_gateway_id:
                return

        element['bpmn_markings'].append({
            'type': marking_type,
            'gateway_id': associated_gateway_id
        })

    def get_bpmn_markings(self, element_id: str) -> list:
        element = self.get_element(element_id)
        if element:
            return element.get('bpmn_markings', [])
        return []

    def add_sequence_flow(self, flow_data):
        if 'id' not in flow_data:
            return
        self.sequence_flows[flow_data['id']] = flow_data
        source_id = flow_data.get('source_ref')
        target_id = flow_data.get('target_ref')
        if source_id and source_id in self.elements:
            self.elements[source_id].setdefault(
                'outgoing_flow_ids', []).append(flow_data['id'])
        if target_id and target_id in self.elements:
            self.elements[target_id].setdefault(
                'incoming_flow_ids', []).append(flow_data['id'])

    def get_element(self, element_id):
        return self.elements.get(element_id)

    def get_sequence_flow(self, flow_id):
        return self.sequence_flows.get(flow_id)

    def get_successors(self, element_id):
        element = self.get_element(element_id)
        if not element or 'outgoing_flow_ids' not in element:
            return []
        successors = []
        for flow_id in element['outgoing_flow_ids']:
            flow = self.get_sequence_flow(flow_id)
            if flow and flow.get('target_ref') and self.get_element(flow.get('target_ref')):
                successors.append(flow['target_ref'])
        return successors

    def get_predecessors(self, element_id):
        element = self.get_element(element_id)
        if not element or 'incoming_flow_ids' not in element:
            return []
        predecessors = []
        for flow_id in element['incoming_flow_ids']:
            flow = self.get_sequence_flow(flow_id)
            if flow and flow.get('source_ref') and self.get_element(flow.get('source_ref')):
                predecessors.append(flow['source_ref'])
        return predecessors


class DCRData:
    def __init__(self, process_id=None, process_name=None):
        self.process_id = process_id
        self.process_name = process_name
        self.events = {}
        self.relations = []
        self._valid_event_markings = {'p', 'i', 'e'}
        self._valid_relation_types = {
            'condition', 'response', 'inclusion', 'exclusion', 'milestone'}

    def add_event(self, event_id, label, initial_marking=None):
        if not event_id:
            raise ValueError("Event ID cannot be None or empty.")

        current_initial_marking = initial_marking if initial_marking is not None else set()

        if event_id in self.events:
            if self.events[event_id]['label'] != label or \
               self.events[event_id]['initial_marking'] != current_initial_marking:
                self.events[event_id]['label'] = label
                self.events[event_id]['initial_marking'] = current_initial_marking
            return

        validated_initial_marking = set()
        if initial_marking is not None:
            if not isinstance(initial_marking, set):
                raise ValueError(
                    "Initial marking must be a set (e.g., {'p', 'i'}).")
            for mark in initial_marking:
                if mark not in self._valid_event_markings:
                    raise ValueError(
                        f"Invalid marking '{mark}' for event '{event_id}'. "
                        f"Must be one of {self._valid_event_markings}."
                    )
                validated_initial_marking.add(mark)

        self.events[event_id] = {
            'label': label,
            'initial_marking': validated_initial_marking
        }

    def add_relation(self, source_event_id, target_event_id, relation_type):
        if source_event_id not in self.events:
            return
        if target_event_id not in self.events:
            return

        if relation_type not in self._valid_relation_types:
            raise ValueError(
                f"Invalid relation type '{relation_type}'. "
                f"Must be one of {self._valid_relation_types}."
            )

        for rel in self.relations:
            if rel['source_id'] == source_event_id and \
               rel['target_id'] == target_event_id and \
               rel['type'] == relation_type:
                return

        self.relations.append({
            'source_id': source_event_id,
            'target_id': target_event_id,
            'type': relation_type
        })

    def get_event_details(self, event_id):
        return self.events.get(event_id)

    def get_relations_for_event(self, event_id, as_source=True, as_target=True):
        connected_relations = []
        for rel in self.relations:
            is_relevant = False
            if as_source and rel['source_id'] == event_id:
                is_relevant = True
            if as_target and rel['target_id'] == event_id:
                is_relevant = True
            if is_relevant:
                connected_relations.append(rel)
        return connected_relations

    def __str__(self):
        return (f"DCRData(process_id='{self.process_id}', name='{self.process_name}', "
                f"events_count={len(self.events)}, relations_count={len(self.relations)})")


class BPMNAnalyzer:
    def __init__(self):
        self.bpmn_data = None
        self._xml_namespaces = {
            'bpmn': 'http://www.omg.org/spec/BPMN/20100524/MODEL'}

    def _get_element_tag(self, tag_name):
        if '' in self._xml_namespaces and self._xml_namespaces[''] == 'http://www.omg.org/spec/BPMN/20100524/MODEL':
            return f"{{{self._xml_namespaces['']}}}{tag_name}"
        prefix_to_use = 'bpmn'
        for prefix, uri in self._xml_namespaces.items():
            if uri == 'http://www.omg.org/spec/BPMN/20100524/MODEL' and prefix:
                prefix_to_use = prefix
                break
        if prefix_to_use == 'bpmn' and prefix_to_use not in self._xml_namespaces and not any(self._xml_namespaces.values()):
            return tag_name
        return f"{{{self._xml_namespaces.get(prefix_to_use, 'http://www.omg.org/spec/BPMN/20100524/MODEL')}}}{tag_name}"

    def load_from_xml(self, filepath):
        try:
            tree = ET.parse(filepath)
            root = tree.getroot()
        except ET.ParseError as e:
            return False, f"XML Parsing Error: {e}"
        except FileNotFoundError:
            return False, f"Error: File '{filepath}' not found."

        bpmn_ns_uri = "http://www.omg.org/spec/BPMN/20100524/MODEL"
        root_tag_ns = root.tag.split('}')[0][1:] if '}' in root.tag else None
        self._xml_namespaces = {}
        if hasattr(root, 'nsmap'):
            for prefix, uri in root.nsmap.items():
                self._xml_namespaces[prefix if prefix else ''] = uri
        if not self._xml_namespaces and root_tag_ns:
            self._xml_namespaces[''] = root_tag_ns

        found_bpmn_ns = any(
            uri == bpmn_ns_uri for uri in self._xml_namespaces.values())
        if not found_bpmn_ns:
            if '' not in self._xml_namespaces and 'bpmn' not in self._xml_namespaces:
                self._xml_namespaces['bpmn'] = bpmn_ns_uri

        process_tag_str = self._get_element_tag('process')
        process_element = root.find(
            process_tag_str, self._xml_namespaces if '' not in self._xml_namespaces else None)
        if process_element is None:
            process_element = root.find(
                f"{{http://www.omg.org/spec/BPMN/20100524/MODEL}}process")
        if process_element is None:
            stripped_tag = 'process'
            if '}' in process_tag_str:
                stripped_tag = process_tag_str.split('}', 1)[1]
            for child in root:
                child_stripped_tag = child.tag
                if '}' in child_stripped_tag:
                    child_stripped_tag = child_stripped_tag.split('}', 1)[1]
                if child_stripped_tag == stripped_tag:
                    process_element = child
                    break
            if process_element is None:
                return False, f"<process> element not found. Check XML structure/namespaces. Namespaces found: {self._xml_namespaces}"

        process_id = process_element.get('id')
        process_name = process_element.get('name')
        self.bpmn_data = BPMNData(process_id, process_name)

        element_types = ['task', 'userTask', 'serviceTask', 'scriptTask', 'manualTask', 'businessRuleTask', 'sendTask', 'receiveTask',
                         'startEvent', 'endEvent',
                         'parallelGateway', 'exclusiveGateway', 'inclusiveGateway',
                         'complexGateway', 'eventBasedGateway']
        for elem_type_name in element_types:
            elem_tag = self._get_element_tag(elem_type_name)
            elements_found = process_element.findall(elem_tag, self._xml_namespaces if '' not in self._xml_namespaces and any(
                p for p in self._xml_namespaces if p) else None)
            if not elements_found and self._xml_namespaces.get('') == bpmn_ns_uri:
                elements_found = process_element.findall(
                    elem_tag.split('}', 1)[1] if '}' in elem_tag else elem_tag)

            for xml_elem in elements_found:
                element_data = {
                    'id': xml_elem.get('id'), 'name': xml_elem.get('name', ''), 'type': elem_type_name,
                    'label': xml_elem.get('name', ''), 'incoming_flow_ids': [], 'outgoing_flow_ids': [],
                }
                if not element_data['id']:
                    continue

                if 'task' in elem_type_name.lower():
                    element_data['base_type'] = 'task'
                else:
                    element_data['base_type'] = elem_type_name

                if elem_type_name.endswith('Gateway'):
                    element_data.update({'gateway_type': elem_type_name.replace('Gateway', ''),
                                         'gateway_direction': None,
                                         'paired_gateway_id': None,
                                         'loop_type': None})
                self.bpmn_data.add_element(element_data)

        flow_tag = self._get_element_tag('sequenceFlow')
        flows_found = process_element.findall(flow_tag, self._xml_namespaces if '' not in self._xml_namespaces and any(
            p for p in self._xml_namespaces if p) else None)
        if not flows_found and self._xml_namespaces.get('') == bpmn_ns_uri:
            flows_found = process_element.findall(
                flow_tag.split('}', 1)[1] if '}' in flow_tag else flow_tag)

        for xml_flow in flows_found:
            flow_data = {
                'id': xml_flow.get('id'), 'name': xml_flow.get('name', ''), 'type': 'sequenceFlow',
                'source_ref': xml_flow.get('sourceRef'), 'target_ref': xml_flow.get('targetRef'), 'expression': None
            }
            if not all([flow_data['id'], flow_data['source_ref'], flow_data['target_ref']]):
                continue

            condition_expr_tag = self._get_element_tag('conditionExpression')
            condition_xml = xml_flow.find(condition_expr_tag, self._xml_namespaces if '' not in self._xml_namespaces and any(
                p for p in self._xml_namespaces if p) else None)
            if condition_xml is None and self._xml_namespaces.get('') == bpmn_ns_uri:
                condition_xml = xml_flow.find(condition_expr_tag.split(
                    '}', 1)[1] if '}' in condition_expr_tag else condition_expr_tag)

            if condition_xml is not None and condition_xml.text:
                flow_data['expression'] = condition_xml.text.strip()
            self.bpmn_data.add_sequence_flow(flow_data)
        return True, None

    def _determine_gateway_directions(self):
        if not self.bpmn_data:
            return
        for _, element in self.bpmn_data.elements.items():
            if element['type'].endswith('Gateway'):
                num_incoming = len(element.get('incoming_flow_ids', []))
                num_outgoing = len(element.get('outgoing_flow_ids', []))
                if num_incoming == 1 and num_outgoing > 1:
                    element['gateway_direction'] = 'split'
                elif num_incoming > 1 and num_outgoing == 1:
                    element['gateway_direction'] = 'join'
                elif num_incoming == 1 and num_outgoing == 1:
                    element['gateway_direction'] = 'routing_decision_point'
                else:
                    element['gateway_direction'] = 'undefined_or_complex'

    def _is_path_between(self, start_node_id, end_node_id, avoid_nodes=None, max_depth=30):
        if start_node_id == end_node_id:
            return True
        effective_avoid_nodes = set(avoid_nodes) if avoid_nodes else set()
        q = deque([(start_node_id, 0)])
        visited_bfs = {start_node_id}
        while q:
            curr_id, depth = q.popleft()
            if depth >= max_depth:
                continue

            for succ_id in self.bpmn_data.get_successors(curr_id):
                if succ_id == end_node_id:
                    return True
                if succ_id not in visited_bfs and succ_id not in effective_avoid_nodes:
                    visited_bfs.add(succ_id)
                    q.append((succ_id, depth + 1))
        return False

    def _check_loop_pairing_candidate(self, j_entry_id, s_cond_id):
        j_gw = self.bpmn_data.get_element(j_entry_id)
        s_gw = self.bpmn_data.get_element(s_cond_id)

        if not (j_gw and s_gw and j_gw['gateway_type'] == 'exclusive' and s_gw['gateway_type'] == 'exclusive' and
                j_gw['gateway_direction'] == 'join' and s_gw['gateway_direction'] == 'split' and
                j_gw['paired_gateway_id'] is None and s_gw['paired_gateway_id'] is None):
            return False, set()

        j_succs = self.bpmn_data.get_successors(j_entry_id)
        if len(j_succs) != 1:
            return False, set()
        body_entry_node = j_succs[0]

        if not self._is_path_between(body_entry_node, s_cond_id, avoid_nodes={j_entry_id}):
            return False, set()

        s_succs = self.bpmn_data.get_successors(s_cond_id)
        if len(s_succs) < 2:
            return False, set()

        path_back_to_join_exists = any(self._is_path_between(
            s_branch_start, j_entry_id, avoid_nodes={s_cond_id}) for s_branch_start in s_succs)
        if not path_back_to_join_exists:
            return False, set()

        path_exits_loop_exists = any(
            not self._is_path_between(s_branch_start, j_entry_id, avoid_nodes={s_cond_id}) and
            (s_branch_start == j_entry_id or not self._is_path_between(
                s_branch_start, s_cond_id, avoid_nodes={j_entry_id}))
            for s_branch_start in s_succs
        )
        if not path_exits_loop_exists:
            return False, set()

        q_body = deque([body_entry_node])
        visited_body_collection = {j_entry_id, body_entry_node}
        body_nodes = set()
        if body_entry_node != s_cond_id:
            body_nodes.add(body_entry_node)

        while q_body:
            curr_body_node_id = q_body.popleft()
            if curr_body_node_id == s_cond_id:
                continue

            for succ_node_id in self.bpmn_data.get_successors(curr_body_node_id):
                if succ_node_id not in visited_body_collection:
                    visited_body_collection.add(succ_node_id)
                    if succ_node_id != s_cond_id:
                        body_nodes.add(succ_node_id)
                    q_body.append(succ_node_id)

        return True, body_nodes

    def _get_region_nodes_and_validate_paths(self, s_gw_id, j_gw_id):
        s_gw = self.bpmn_data.get_element(s_gw_id)
        s_successors = self.bpmn_data.get_successors(s_gw_id)
        if not s_successors:
            return False, set(), False

        all_intermediate_nodes_in_region = set()
        all_paths_reach_join_cleanly = True

        for branch_start_node_id in s_successors:
            q = deque([(branch_start_node_id, [s_gw_id, branch_start_node_id])])
            path_reaches_join_for_this_branch = False
            visited_in_branch_exploration = {s_gw_id}

            while q:
                curr_node_id, path = q.popleft()

                if curr_node_id == j_gw_id:
                    path_reaches_join_for_this_branch = True
                    all_intermediate_nodes_in_region.update(
                        p_node for p_node in path[1:-1])
                    break

                curr_node_element = self.bpmn_data.get_element(curr_node_id)
                if curr_node_element['type'].endswith('Gateway') and \
                   curr_node_element.get('gateway_type') == s_gw.get('gateway_type') and \
                   curr_node_element.get('paired_gateway_id') is None and \
                   curr_node_id != s_gw_id and curr_node_id != j_gw_id and \
                   curr_node_element.get('gateway_direction') in ['split', 'join']:
                    all_paths_reach_join_cleanly = False
                    break

                if curr_node_id not in {s_gw_id, j_gw_id}:
                    all_intermediate_nodes_in_region.add(curr_node_id)

                for succ_id in self.bpmn_data.get_successors(curr_node_id):
                    if succ_id in path:
                        all_paths_reach_join_cleanly = False
                        break
                    if succ_id not in visited_in_branch_exploration or succ_id == j_gw_id:
                        if succ_id != j_gw_id:
                            visited_in_branch_exploration.add(succ_id)
                        q.append((succ_id, path + [succ_id]))
                if not all_paths_reach_join_cleanly:
                    break

            if not path_reaches_join_for_this_branch:
                all_paths_reach_join_cleanly = False
                break

        if not all_paths_reach_join_cleanly:
            return False, set(), False

        region_is_structurally_clean = not any(
            (el := self.bpmn_data.get_element(el_id)) and
            el['type'].endswith('Gateway') and
            el.get('gateway_type') == s_gw.get('gateway_type') and
            el.get('paired_gateway_id') is None and
            el.get('gateway_direction') in ['split', 'join']
            for el_id in all_intermediate_nodes_in_region
        )
        return True, all_intermediate_nodes_in_region, region_is_structurally_clean

    def _trace_and_annotate_inclusive_path(self, origin_gis_flow_id, start_node_id, paired_gij_id):
        if not self.bpmn_data or not self.bpmn_data.get_element(start_node_id):
            return

        q = deque([start_node_id])
        visited_for_this_trace = {start_node_id}

        while q:
            current_node_id = q.popleft()
            current_element = self.bpmn_data.get_element(current_node_id)

            if not current_element:
                continue

            is_direct_predecessor_of_gij = False
            if current_element.get('outgoing_flow_ids'):
                for out_flow_id in current_element['outgoing_flow_ids']:
                    flow = self.bpmn_data.get_sequence_flow(out_flow_id)
                    if flow and flow.get('target_ref') == paired_gij_id:
                        is_direct_predecessor_of_gij = True
                        break

            if is_direct_predecessor_of_gij:
                if '_inclusive_path_origin_flow_id' not in current_element or \
                   current_element.get('_inclusive_path_origin_flow_id') is None:  # Annotate if not already set
                    current_element['_inclusive_path_origin_flow_id'] = origin_gis_flow_id

            if current_node_id == paired_gij_id:
                continue

            for successor_id in self.bpmn_data.get_successors(current_node_id):
                if successor_id not in visited_for_this_trace:
                    # Prevent traversing into a different paired structure if identified
                    # This is a basic check; more sophisticated region control might be needed
                    # For example, if successor is a GIS and its paired GIJ is not our current paired_gij_id
                    # For now, allow traversal unless it's the GIJ itself or already visited.
                    visited_for_this_trace.add(successor_id)
                    q.append(successor_id)

    def _annotate_inclusive_paths(self):
        if not self.bpmn_data:
            return

        for gw_id, gw_element in self.bpmn_data.elements.items():
            if gw_element.get('gateway_type') == 'inclusive' and \
               gw_element.get('gateway_direction') == 'split' and \
               gw_element.get('paired_gateway_id'):

                paired_gij_id = gw_element.get('paired_gateway_id')
                if paired_gij_id not in self.bpmn_data.elements:
                    continue

                for gis_outgoing_flow_id in gw_element.get('outgoing_flow_ids', []):
                    gis_outgoing_flow = self.bpmn_data.get_sequence_flow(
                        gis_outgoing_flow_id)
                    if gis_outgoing_flow and gis_outgoing_flow.get('target_ref'):
                        start_node_after_gis = gis_outgoing_flow.get(
                            'target_ref')
                        self._trace_and_annotate_inclusive_path(gis_outgoing_flow_id,
                                                                start_node_after_gis,
                                                                paired_gij_id)

    def pair_gateways(self):
        if not self.bpmn_data:
            return
        self._determine_gateway_directions()

        all_gateways = [el for _, el in self.bpmn_data.elements.items(
        ) if el['type'].endswith('Gateway')]
        for gw_element in all_gateways:
            gw_element.setdefault('loop_type', None)

        while True:
            made_a_pairing_in_this_pass = False
            candidate_loops = []
            unpaired_exclusive_joins = [gw for gw in all_gateways if gw.get('gateway_type') == 'exclusive' and gw.get(
                'gateway_direction') == 'join' and gw.get('paired_gateway_id') is None]
            unpaired_exclusive_splits = [gw for gw in all_gateways if gw.get('gateway_type') == 'exclusive' and gw.get(
                'gateway_direction') == 'split' and gw.get('paired_gateway_id') is None]

            for j_gw_loop in unpaired_exclusive_joins:
                for s_gw_loop in unpaired_exclusive_splits:
                    if j_gw_loop['id'] == s_gw_loop['id']:
                        continue
                    is_loop, body_nodes = self._check_loop_pairing_candidate(
                        j_gw_loop['id'], s_gw_loop['id'])
                    if is_loop:
                        contains_unpaired_gw_in_body = any(
                            (el_body := self.bpmn_data.get_element(node_id_body)) and el_body['type'].endswith('Gateway') and
                            el_body.get('paired_gateway_id') is None and el_body.get(
                                'gateway_direction') in ['split', 'join']
                            for node_id_body in body_nodes
                        )
                        if not contains_unpaired_gw_in_body:
                            candidate_loops.append(
                                (j_gw_loop['id'], s_gw_loop['id'], len(body_nodes)))

            if candidate_loops:
                candidate_loops.sort(key=lambda x: x[2])
                best_j_id, best_s_id, _ = candidate_loops[0]
                if self.bpmn_data.elements[best_j_id]['paired_gateway_id'] is None and \
                   self.bpmn_data.elements[best_s_id]['paired_gateway_id'] is None:
                    self.bpmn_data.elements[best_j_id].update(
                        {'paired_gateway_id': best_s_id, 'loop_type': 'loop_entry_join'})
                    self.bpmn_data.elements[best_s_id].update(
                        {'paired_gateway_id': best_j_id, 'loop_type': 'loop_condition_split'})
                    made_a_pairing_in_this_pass = True
                    continue

            candidate_sese_pairs = []
            unpaired_splits = [gw for gw in all_gateways if gw.get('gateway_direction') == 'split' and gw.get(
                'paired_gateway_id') is None and gw.get('loop_type') is None]
            for s_gw_sese in unpaired_splits:
                potential_joins = [
                    gw for gw in all_gateways if gw.get('gateway_direction') == 'join' and
                    gw.get('paired_gateway_id') is None and gw.get('loop_type') is None and
                    gw.get('gateway_type') == s_gw_sese.get('gateway_type')
                ]
                for j_gw_sese in potential_joins:
                    if s_gw_sese['id'] == j_gw_sese['id']:
                        continue
                    paths_valid, region_nodes, region_is_clean = self._get_region_nodes_and_validate_paths(
                        s_gw_sese['id'], j_gw_sese['id'])
                    if paths_valid and region_is_clean:
                        candidate_sese_pairs.append(
                            (s_gw_sese['id'], j_gw_sese['id'], len(region_nodes)))

            if candidate_sese_pairs:
                candidate_sese_pairs.sort(key=lambda x: x[2])
                best_s_id_sese, best_j_id_sese, _ = candidate_sese_pairs[0]
                if self.bpmn_data.elements[best_s_id_sese]['paired_gateway_id'] is None and \
                   self.bpmn_data.elements[best_j_id_sese]['paired_gateway_id'] is None:
                    self.bpmn_data.elements[best_s_id_sese]['paired_gateway_id'] = best_j_id_sese
                    self.bpmn_data.elements[best_j_id_sese]['paired_gateway_id'] = best_s_id_sese
                    made_a_pairing_in_this_pass = True
                    continue

            if not made_a_pairing_in_this_pass:
                break

        self._populate_gateway_markings()
        self._annotate_inclusive_paths()

    def _populate_gateway_markings(self):
        if not self.bpmn_data or not self.bpmn_data.elements:
            return

        for gw_id, gw_element in self.bpmn_data.elements.items():
            if not gw_element['type'].endswith('Gateway'):
                continue
            gateway_direction = gw_element.get('gateway_direction')

            if gateway_direction == 'split':
                for flow_id in gw_element.get('incoming_flow_ids', []):
                    flow = self.bpmn_data.get_sequence_flow(flow_id)
                    if flow and flow.get('source_ref'):
                        self.bpmn_data.add_bpmn_marking(
                            flow.get('source_ref'), "S-", gw_id)
                for flow_id in gw_element.get('outgoing_flow_ids', []):
                    flow = self.bpmn_data.get_sequence_flow(flow_id)
                    if flow and flow.get('target_ref'):
                        self.bpmn_data.add_bpmn_marking(
                            flow.get('target_ref'), "S+", gw_id)
            elif gateway_direction == 'join':
                for flow_id in gw_element.get('incoming_flow_ids', []):
                    flow = self.bpmn_data.get_sequence_flow(flow_id)
                    if flow and flow.get('source_ref'):
                        self.bpmn_data.add_bpmn_marking(
                            flow.get('source_ref'), "J-", gw_id)
                for flow_id in gw_element.get('outgoing_flow_ids', []):
                    flow = self.bpmn_data.get_sequence_flow(flow_id)
                    if flow and flow.get('target_ref'):
                        self.bpmn_data.add_bpmn_marking(
                            flow.get('target_ref'), "J+", gw_id)

    def perform_precondition_checks(self):
        if not self.bpmn_data:
            return False, ["BPMN data not loaded."]
        error_messages = []
        overall_passed = True

        start_events = [
            e for e in self.bpmn_data.elements.values() if e['type'] == 'startEvent']
        if len(start_events) != 1:
            overall_passed = False
            msg = f"Expected 1 Start Event, found {len(start_events)}."
            error_messages.append(msg)
            for i, e_item in enumerate(start_events):
                error_messages.append(
                    f"  - Start Event {i+1}: ID '{e_item['id']}', Name: '{e_item.get('name', 'N/A')}'")

        end_events = [e for e in self.bpmn_data.elements.values()
                      if e['type'] == 'endEvent']
        if not end_events:
            overall_passed = False
            error_messages.append("Expected at least 1 End Event, found 0.")

        gateways = [el for el in self.bpmn_data.elements.values()
                    if el['type'].endswith('Gateway')]
        unpaired_structural_gateways = []
        sese_violations = []

        for gw in gateways:
            direction = gw.get('gateway_direction')
            num_in = len(gw.get('incoming_flow_ids', []))
            num_out = len(gw.get('outgoing_flow_ids', []))
            is_structural_for_pairing = gw.get('gateway_type') in [
                'parallel', 'exclusive', 'inclusive'] and direction in ['split', 'join']

            if is_structural_for_pairing and gw.get('paired_gateway_id') is None and gw.get('loop_type') is None:
                overall_passed = False
                unpaired_structural_gateways.append(
                    f"  - ID: {gw['id']}, Name: {gw.get('name') or gw['id']}, Type: {gw.get('gateway_type')}, Dir: {direction}. Expected pair.")

            sese_reason = ""
            if direction == 'split' and not (num_in == 1 and num_out > 1):
                sese_reason = f"Split violation (In:{num_in}/Out:{num_out})"
            elif direction == 'join' and not (num_in > 1 and num_out == 1):
                sese_reason = f"Join violation (In:{num_in}/Out:{num_out})"
            elif direction == 'routing_decision_point' and gw.get('gateway_type') in ['parallel', 'inclusive']:
                sese_reason = f"1-in/1-out for {gw.get('gateway_type')} is unusual."
            elif direction == 'undefined_or_complex':
                sese_reason = f"Undefined flow (In:{num_in}/Out:{num_out})"

            if sese_reason and ((is_structural_for_pairing and gw.get('paired_gateway_id') is None and gw.get('loop_type') is None) or direction == 'undefined_or_complex'):
                overall_passed = False
                sese_violations.append(
                    f"  - ID: {gw['id']}, Name: {gw.get('name') or gw['id']}, Type: {gw.get('gateway_type')}, Dir: {direction or 'N/A'}. Reason: {sese_reason}")

        if unpaired_structural_gateways:
            error_messages.append(
                "Unpaired structural gateways (should form SESE/loops):")
            error_messages.extend(unpaired_structural_gateways)
        if sese_violations:
            error_messages.append("Gateways violating SESE flow principles:")
            error_messages.extend(sese_violations)

        return overall_passed, error_messages


class BPMNTranslator:
    def __init__(self, bpmn_data: BPMNData):
        if not isinstance(bpmn_data, BPMNData):
            raise TypeError("bpmn_data must be an instance of BPMNData.")
        self.bpmn_data = bpmn_data
        dcr_process_id = f"dcr_from_{bpmn_data.process_id}" if bpmn_data.process_id else "dcr_process"
        dcr_process_name = f"DCR graph for {bpmn_data.process_name}" if bpmn_data.process_name else "DCR Process"
        self.dcr_data = DCRData(process_id=dcr_process_id,
                                process_name=dcr_process_name)
        self.bpmn_to_dcr_event_map = {}
        self.gateway_pair_suffixes = self._assign_gateway_pair_suffixes()
        self.parallel_state_global_counter = 1
        self.inclusive_state_global_counter = 1

    def _assign_gateway_pair_suffixes(self) -> dict:
        pair_suffixes_map = {}
        processed_for_suffix = set()
        pair_counter = 1
        sorted_gateway_ids = sorted([
            gw_id for gw_id, gw_element in self.bpmn_data.elements.items()
            if gw_element['type'].endswith('Gateway') and gw_element.get('gateway_direction') in ['split', 'join']
        ])

        for gw_id in sorted_gateway_ids:
            if gw_id in processed_for_suffix:
                continue
            gw_element = self.bpmn_data.get_element(gw_id)
            paired_id = gw_element.get('paired_gateway_id')
            if paired_id and paired_id in self.bpmn_data.elements:
                paired_element = self.bpmn_data.get_element(paired_id)
                if paired_element and paired_element.get('paired_gateway_id') == gw_id and paired_id not in processed_for_suffix:
                    suffix = f"\n[Pair {pair_counter}]"
                    pair_suffixes_map[gw_id] = suffix
                    pair_suffixes_map[paired_id] = suffix
                    processed_for_suffix.add(gw_id)
                    processed_for_suffix.add(paired_id)
                    pair_counter += 1
        return pair_suffixes_map

    def _has_bpmn_marking(self, bpmn_element_id: str, marking_type: str, associated_gateway_id: str = None) -> bool:
        element = self.bpmn_data.get_element(bpmn_element_id)
        if not element:
            return False
        for mark in element.get('bpmn_markings', []):
            if associated_gateway_id:
                if mark['type'] == marking_type and mark['gateway_id'] == associated_gateway_id:
                    return True
            else:
                if mark['type'] == marking_type:
                    return True
        return False

    def _is_common_task(self, bpmn_element_id: str) -> bool:
        if not bpmn_element_id:
            return False
        element = self.bpmn_data.get_element(bpmn_element_id)
        if not element or element.get('base_type') != 'task':
            return False
        bpmn_markings = element.get('bpmn_markings', [])
        if not bpmn_markings:
            return True
        return not any(mark.get('type') in ["S+", "S-", "J+", "J-"] for mark in bpmn_markings)

    def _get_or_create_helper_dcr_event(self, base_id_context: str, helper_type_prefix: str, dcr_label_text: str, dcr_initial_markings: set, unique_part_for_id=""):
        if not isinstance(unique_part_for_id, str):
            unique_part_for_id = str(unique_part_for_id)
        event_dcr_id = ""
        if helper_type_prefix == "x_expr":
            expr_hash_content = base_id_context + "_" + unique_part_for_id
            expr_hash = hashlib.md5(expr_hash_content.encode()).hexdigest()[:8]
            event_dcr_id = f"expr_{expr_hash}"
        else:
            event_dcr_id = f"{helper_type_prefix}_{base_id_context}{'_' + unique_part_for_id if unique_part_for_id else ''}"

        is_new_event = event_dcr_id not in self.dcr_data.events
        try:
            self.dcr_data.add_event(
                event_dcr_id, dcr_label_text, dcr_initial_markings)
            if is_new_event:  # Apply self-exclusion for all newly created DCR events per rule 11
                self.dcr_data.add_relation(
                    event_dcr_id, event_dcr_id, 'exclusion')
        except ValueError as e:
            return None
        return event_dcr_id

    def _get_or_create_parallel_state_event(self, associated_jn_minus_bpmn_id: str, associated_gpj_bpmn_id: str) -> str:
        i = self.parallel_state_global_counter
        self.parallel_state_global_counter += 1
        pair_suffix = self.gateway_pair_suffixes.get(
            associated_gpj_bpmn_id, "")
        base_label = f"✖✖✖✖✖✖✖✖\n✖✖✖✖✖✖✖✖\nParallelState {i}"
        jn_name_element = self.bpmn_data.get_element(
            associated_jn_minus_bpmn_id)
        jn_name = jn_name_element.get(
            'name', associated_jn_minus_bpmn_id) if jn_name_element else associated_jn_minus_bpmn_id
        dcr_label_text = f"{base_label}\n{pair_suffix}"

        return self._get_or_create_helper_dcr_event(
            base_id_context=associated_jn_minus_bpmn_id,
            helper_type_prefix="l_state_jn",
            dcr_label_text=dcr_label_text,
            dcr_initial_markings={'i'},
            unique_part_for_id=str(i)
        )

    def _get_or_create_inclusive_state_event(self, associated_jn_minus_bpmn_id: str, associated_gij_bpmn_id: str) -> str:
        i = self.inclusive_state_global_counter
        self.inclusive_state_global_counter += 1
        pair_suffix = self.gateway_pair_suffixes.get(
            associated_gij_bpmn_id, "")
        base_label = f"✖✖✖✖✖✖✖✖\n✖✖✖✖✖✖✖✖\nInclusiveState {i}"
        jn_name_element = self.bpmn_data.get_element(
            associated_jn_minus_bpmn_id)
        jn_name = jn_name_element.get(
            'name', associated_jn_minus_bpmn_id) if jn_name_element else associated_jn_minus_bpmn_id
        dcr_label_text = f"{base_label}{pair_suffix}"

        return self._get_or_create_helper_dcr_event(
            base_id_context=associated_jn_minus_bpmn_id,
            helper_type_prefix="n_state_jn",
            dcr_label_text=dcr_label_text,
            dcr_initial_markings=set(),
            unique_part_for_id=str(i)
        )

    def _get_or_create_expression_event(self, expression_text: str, associated_flow_bpmn_id: str, sequence_flow_name: str) -> str:
        final_dcr_label = ""
        id_hash_content_unique_part = ""
        stripped_expression = expression_text.strip() if expression_text else ""

        if not stripped_expression:
            final_dcr_label = f"[[Expression]]\n{sequence_flow_name}" if sequence_flow_name else f"[[Expr ID]]\n{associated_flow_bpmn_id}"
            id_hash_content_unique_part = f"empty_on_{associated_flow_bpmn_id}"
        else:
            final_dcr_label = stripped_expression
            id_hash_content_unique_part = stripped_expression

        return self._get_or_create_helper_dcr_event(
            associated_flow_bpmn_id,
            "x_expr",
            final_dcr_label,
            set(),
            unique_part_for_id=id_hash_content_unique_part
        )

    def map_bpmn_objects_to_dcr_events(self):
        if not self.bpmn_data or not self.bpmn_data.elements:
            return

        for bpmn_id, bpmn_element in self.bpmn_data.elements.items():
            dcr_event_id = bpmn_id
            final_label = ""
            base_label = bpmn_element.get(
                'name') or bpmn_element.get('label') or dcr_event_id
            initial_marking = set()
            element_type = bpmn_element.get('type')

            if element_type == 'startEvent':
                base_label = 'Start Event'
                initial_marking = {'p', 'i'}
            elif element_type == 'endEvent':
                base_label = 'End Event'
            elif bpmn_element.get('base_type') == 'task':
                base_label = bpmn_element.get(
                    'name') or bpmn_element.get('label') or dcr_event_id
            elif element_type and element_type.endswith('Gateway'):
                gw_specific_type = bpmn_element.get('gateway_type', '')
                gw_direction = bpmn_element.get('gateway_direction', '')
                gw_type_cap = gw_specific_type.capitalize() if gw_specific_type else "Gateway"
                gw_dir_cap = gw_direction.capitalize().replace("_", " ") if gw_direction else ""

                base_label = f"{gw_type_cap} {gw_dir_cap}".strip()
                if not gw_dir_cap and "Gateway" not in base_label:
                    base_label += " Gateway"

                pair_suffix = self.gateway_pair_suffixes.get(bpmn_id, "")
                final_label = f"{base_label}{pair_suffix}"
            else:
                continue

            if not final_label:
                final_label = base_label

            try:
                self.dcr_data.add_event(
                    dcr_event_id, final_label, initial_marking)
                self.bpmn_to_dcr_event_map[bpmn_id] = dcr_event_id
                self.dcr_data.add_relation(
                    dcr_event_id, dcr_event_id, 'exclusion')
            except ValueError as e:
                pass

    def generic_relation_mapping(self):
        if not self.bpmn_data or not self.bpmn_data.sequence_flows:
            return

        for _, bpmn_flow in self.bpmn_data.sequence_flows.items():
            source_bpmn_id = bpmn_flow.get('source_ref')
            target_bpmn_id = bpmn_flow.get('target_ref')
            if not source_bpmn_id or not target_bpmn_id:
                continue

            source_element = self.bpmn_data.get_element(source_bpmn_id)
            target_element = self.bpmn_data.get_element(target_bpmn_id)
            if not source_element or not target_element:
                continue

            source_dcr_id = self.bpmn_to_dcr_event_map.get(source_bpmn_id)
            target_dcr_id = self.bpmn_to_dcr_event_map.get(target_bpmn_id)
            if not source_dcr_id or not target_dcr_id:
                continue

            apply_response_inclusion = False
            if source_element['type'] == 'startEvent':
                apply_response_inclusion = True
            elif target_element['type'] == 'endEvent':
                apply_response_inclusion = True
            elif self._is_common_task(source_bpmn_id) and target_element.get('base_type') == 'task':
                apply_response_inclusion = True
            elif source_element.get('base_type') == 'task' and self._is_common_task(target_bpmn_id):
                apply_response_inclusion = True
            elif source_element.get('base_type') == 'task' and target_element.get('base_type') == 'task':
                apply_response_inclusion = True
            elif source_element.get('gateway_direction') == 'join' and self._has_bpmn_marking(target_bpmn_id, "J+", source_bpmn_id):
                apply_response_inclusion = True
            elif target_element.get('gateway_direction') == 'split' and self._has_bpmn_marking(source_bpmn_id, "S-", target_bpmn_id):
                apply_response_inclusion = True
            # For Split Gateway -> S+ Element:
            # If gateway is EXCLUSIVE or PARALLEL, apply direct response/inclusion.
            # If gateway is INCLUSIVE, this direct link is NOT applied here, as inclusive_gateway_mapping handles the e-x-f chain.
            elif source_element.get('gateway_direction') == 'split' and \
                    source_element.get('gateway_type') in ['exclusive', 'parallel'] and \
                    self._has_bpmn_marking(target_bpmn_id, "S+", source_bpmn_id):
                apply_response_inclusion = True
            elif target_element.get('gateway_direction') == 'join' and \
                    self._has_bpmn_marking(source_bpmn_id, "J-", target_bpmn_id):
                # Only for exclusive, others have specific rules
                if target_element.get('gateway_type') == 'exclusive':
                    apply_response_inclusion = True

            if apply_response_inclusion:
                try:
                    self.dcr_data.add_relation(
                        source_dcr_id, target_dcr_id, 'response')
                    self.dcr_data.add_relation(
                        source_dcr_id, target_dcr_id, 'inclusion')
                except ValueError:
                    pass

    def exclusive_gateway_mapping(self):
        if not self.bpmn_data:
            return
        for bpmn_id, bpmn_element in self.bpmn_data.elements.items():
            if bpmn_element.get('gateway_type') == 'exclusive':
                gateway_dcr_id = self.bpmn_to_dcr_event_map.get(bpmn_id)
                if not gateway_dcr_id:
                    continue

                if bpmn_element.get('gateway_direction') == 'split':
                    s_plus_successors_dcr_ids = []
                    for flow_id_succ in bpmn_element.get('outgoing_flow_ids', []):
                        flow_succ = self.bpmn_data.get_sequence_flow(
                            flow_id_succ)
                        if flow_succ:
                            target_bpmn_id_succ = flow_succ.get('target_ref')
                            if target_bpmn_id_succ and self.bpmn_data.get_element(target_bpmn_id_succ) and \
                               self._has_bpmn_marking(target_bpmn_id_succ, "S+", bpmn_id):
                                target_dcr_id = self.bpmn_to_dcr_event_map.get(
                                    target_bpmn_id_succ)
                                if target_dcr_id:
                                    s_plus_successors_dcr_ids.append(
                                        target_dcr_id)

                    for i in range(len(s_plus_successors_dcr_ids)):
                        for j in range(i + 1, len(s_plus_successors_dcr_ids)):
                            try:
                                self.dcr_data.add_relation(
                                    s_plus_successors_dcr_ids[i], s_plus_successors_dcr_ids[j], 'exclusion')
                                self.dcr_data.add_relation(
                                    s_plus_successors_dcr_ids[j], s_plus_successors_dcr_ids[i], 'exclusion')
                            except ValueError:
                                pass

    def parallel_gateway_mapping(self):
        if not self.bpmn_data:
            return
        for bpmn_id, bpmn_element in self.bpmn_data.elements.items():
            if bpmn_element.get('gateway_type') == 'parallel':
                gateway_dcr_id = self.bpmn_to_dcr_event_map.get(bpmn_id)
                if not gateway_dcr_id:
                    continue
                paired_gw_bpmn_id = bpmn_element.get('paired_gateway_id')
                paired_gw_dcr_id = self.bpmn_to_dcr_event_map.get(
                    paired_gw_bpmn_id) if paired_gw_bpmn_id else None

                if bpmn_element.get('gateway_direction') == 'split' and paired_gw_dcr_id:
                    try:
                        self.dcr_data.add_relation(
                            gateway_dcr_id, paired_gw_dcr_id, 'response')
                    except ValueError:
                        pass

                elif bpmn_element.get('gateway_direction') == 'join':
                    for flow_id_inc in bpmn_element.get('incoming_flow_ids', []):
                        flow_inc = self.bpmn_data.get_sequence_flow(
                            flow_id_inc)
                        if flow_inc:
                            predecessor_bpmn_id = flow_inc.get('source_ref')
                            pred_dcr_id = self.bpmn_to_dcr_event_map.get(
                                predecessor_bpmn_id)
                            if pred_dcr_id and self._has_bpmn_marking(predecessor_bpmn_id, "J-", bpmn_id):
                                l_event_dcr_id = self._get_or_create_parallel_state_event(
                                    predecessor_bpmn_id, bpmn_id)
                                if not l_event_dcr_id:
                                    continue
                                try:
                                    self.dcr_data.add_relation(
                                        pred_dcr_id, l_event_dcr_id, 'exclusion')
                                    self.dcr_data.add_relation(
                                        l_event_dcr_id, gateway_dcr_id, 'condition')
                                    self.dcr_data.add_relation(
                                        pred_dcr_id, gateway_dcr_id, 'inclusion')
                                except ValueError:
                                    pass

    def inclusive_gateway_mapping(self):
        if not self.bpmn_data:
            return

        for bpmn_id, bpmn_element in self.bpmn_data.elements.items():  # e.g. GIS or GIJ
            if bpmn_element.get('gateway_type') == 'inclusive':
                gateway_dcr_id = self.bpmn_to_dcr_event_map.get(
                    bpmn_id)  # This is 'e' for GIS, or 'f' for GIJ rules
                if not gateway_dcr_id:
                    continue

                paired_gw_bpmn_id = bpmn_element.get('paired_gateway_id')
                paired_gw_dcr_id = self.bpmn_to_dcr_event_map.get(
                    paired_gw_bpmn_id) if paired_gw_bpmn_id else None

                # --- GIS (Split) Part ---
                if bpmn_element.get('gateway_direction') == 'split':
                    # Rule: GIS ⇢ GIJ ⇒ eresponsef
                    if paired_gw_dcr_id:
                        try:
                            self.dcr_data.add_relation(
                                gateway_dcr_id, paired_gw_dcr_id, 'response')  # e_gis response f_gij
                        except ValueError:
                            pass

                    # Rule: GIS → [Sn+] ⇒ eresponseinclusionxresponseinclusionf
                    # This translates to: e R x and x R f (where R is response/inclusion)
                    # e = gateway_dcr_id (GIS)
                    # x = x_event_dcr_id (Expression for outgoing flow)
                    # f = target_dcr_id (Sn+ element)
                    for flow_id_out in bpmn_element.get('outgoing_flow_ids', []):
                        flow_out = self.bpmn_data.get_sequence_flow(
                            flow_id_out)
                        if flow_out:
                            target_bpmn_sn_plus_id = flow_out.get('target_ref')
                            target_dcr_id_f = self.bpmn_to_dcr_event_map.get(
                                target_bpmn_sn_plus_id)  # This is 'f'

                            if target_dcr_id_f and self._has_bpmn_marking(target_bpmn_sn_plus_id, "S+", bpmn_id):
                                expr_text = flow_out.get('expression', "")
                                flow_name = flow_out.get('name', '')
                                x_event_dcr_id = self._get_or_create_expression_event(
                                    # This is 'x'
                                    expr_text, flow_out['id'], flow_name)

                                if x_event_dcr_id:
                                    try:
                                        # Add e R x relations
                                        self.dcr_data.add_relation(
                                            gateway_dcr_id, x_event_dcr_id, 'response')    # e response x
                                        self.dcr_data.add_relation(
                                            gateway_dcr_id, x_event_dcr_id, 'inclusion')   # e inclusion x

                                        # Add x R f relations
                                        self.dcr_data.add_relation(
                                            x_event_dcr_id, target_dcr_id_f, 'response')  # x response f
                                        self.dcr_data.add_relation(
                                            x_event_dcr_id, target_dcr_id_f, 'inclusion')  # x inclusion f
                                    except ValueError:
                                        pass

                # --- GIJ (Join) Part ---
                elif bpmn_element.get('gateway_direction') == 'join':
                    # gateway_dcr_id is f_gij for these rules
                    for flow_id_in in bpmn_element.get('incoming_flow_ids', []):
                        flow_in = self.bpmn_data.get_sequence_flow(flow_id_in)
                        if flow_in:
                            # e_jn_minus is the DCR event for Jn- element
                            jn_minus_bpmn_id = flow_in.get('source_ref')
                            e_jn_minus_dcr_id = self.bpmn_to_dcr_event_map.get(
                                jn_minus_bpmn_id)
                            jn_minus_element = self.bpmn_data.get_element(
                                jn_minus_bpmn_id)

                            if not e_jn_minus_dcr_id or not jn_minus_element:
                                continue

                            n_event_dcr_id = None  # This is 'n'
                            # Rule: [Jn-] → GIJ ⇒ eexclusionnconditionf, einclusionf
                            # bpmn_id is GIJ here
                            if self._has_bpmn_marking(jn_minus_bpmn_id, "J-", bpmn_id):
                                n_event_dcr_id = self._get_or_create_inclusive_state_event(
                                    jn_minus_bpmn_id, bpmn_id)
                                if n_event_dcr_id:
                                    try:
                                        self.dcr_data.add_relation(
                                            e_jn_minus_dcr_id, n_event_dcr_id, 'exclusion')  # e_jn- exclusion n
                                        self.dcr_data.add_relation(
                                            n_event_dcr_id, gateway_dcr_id, 'condition')   # n condition f_gij
                                        self.dcr_data.add_relation(
                                            e_jn_minus_dcr_id, gateway_dcr_id, 'inclusion')  # e_jn- inclusion f_gij
                                    except ValueError:
                                        pass

                            # For rules x ⇢ n and GIJ ⇢ x, use the x from the GIS's outgoing flow for this path
                            # 'x_gis_originated' is the 'x' from the GIS side
                            origin_gis_flow_id = jn_minus_element.get(
                                '_inclusive_path_origin_flow_id')
                            x_gis_originated_dcr_id = None
                            if origin_gis_flow_id:
                                origin_flow_details = self.bpmn_data.get_sequence_flow(
                                    origin_gis_flow_id)
                                if origin_flow_details:
                                    expr_text = origin_flow_details.get(
                                        'expression', "")
                                    flow_name = origin_flow_details.get(
                                        'name', '')
                                    x_gis_originated_dcr_id = self._get_or_create_expression_event(
                                        expr_text, origin_gis_flow_id, flow_name)

                            if x_gis_originated_dcr_id:
                                # Rule: x ⇢ n = xinclusionn (using x from GIS side)
                                if n_event_dcr_id:  # n must exist for this relation
                                    try:
                                        self.dcr_data.add_relation(
                                            x_gis_originated_dcr_id, n_event_dcr_id, 'inclusion')
                                    except ValueError:
                                        pass

                                # Rule: GIJ ⇢ x ⇒ eexclusionx (using x from GIS side)
                                # gateway_dcr_id is f_gij
                                try:
                                    self.dcr_data.add_relation(
                                        gateway_dcr_id, x_gis_originated_dcr_id, 'exclusion')
                                except ValueError:
                                    pass

    def get_dcr_graph(self) -> DCRData:
        return self.dcr_data


class DCRExporter:
    def __init__(self, dcr_data: DCRData):
        if not isinstance(dcr_data, DCRData):
            raise TypeError("dcr_data must be an instance of DCRData.")
        self.dcr_data = dcr_data
        self.relation_id_counter = 0

    def _generate_relation_id(self) -> str:
        self.relation_id_counter += 1
        return f"Relation_{self.relation_id_counter:07d}"

    def export_to_xml(self, filepath: str):
        dcrgraph_el = ET.Element("dcrgraph")
        specification_el = ET.SubElement(dcrgraph_el, "specification")
        self._add_resources(specification_el)
        self._add_constraints(specification_el)
        self._add_runtime(dcrgraph_el)

        xml_str = ET.tostring(dcrgraph_el, encoding='utf-8', method='xml')
        try:
            dom = minidom.parseString(xml_str)
            pretty_xml_str_lines = dom.toprettyxml(indent="  ").splitlines()
            filtered_lines = [
                line for line in pretty_xml_str_lines if line.strip()]
            final_xml_str = "\n".join(filtered_lines)
        except Exception:
            final_xml_str = xml_str.decode('utf-8')

        if not final_xml_str.startswith("<?xml"):
            final_xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + final_xml_str

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(final_xml_str)
        except IOError:
            pass

    def _add_resources(self, specification_el: ET.Element):
        resources_el = ET.SubElement(specification_el, "resources")
        events_el = ET.SubElement(resources_el, "events")
        event_label_details = []

        for event_id, event_data in self.dcr_data.events.items():
            event_el = ET.SubElement(events_el, "event", id=event_id)
            custom_el = ET.SubElement(event_el, "custom")
            visualization_el = ET.SubElement(custom_el, "visualization")
            ET.SubElement(visualization_el, "location", xLoc="0", yLoc="0")
            ET.SubElement(visualization_el, "size", width="130", height="150")
            label_text = event_data.get('label', event_id)
            event_label_details.append(
                {'event_id': event_id, 'label_text': label_text})

        ET.SubElement(resources_el, "subProcesses")
        labels_el = ET.SubElement(resources_el, "labels")
        unique_label_texts = sorted(list(
            set(item['label_text'] for item in event_label_details if item['label_text'])))
        for label_txt in unique_label_texts:
            ET.SubElement(labels_el, "label", id=label_txt)

        label_mappings_el = ET.SubElement(resources_el, "labelMappings")
        for item in event_label_details:
            if item['label_text']:
                ET.SubElement(label_mappings_el, "labelMapping",
                              eventId=item['event_id'], labelId=item['label_text'])

        ET.SubElement(resources_el, "variables")
        ET.SubElement(resources_el, "expressions")
        variable_accesses_el = ET.SubElement(resources_el, "variableAccesses")
        ET.SubElement(variable_accesses_el, "readAccessess")
        ET.SubElement(variable_accesses_el, "writeAccessess")

    def _add_constraints(self, specification_el: ET.Element):
        constraints_el = ET.SubElement(specification_el, "constraints")
        relation_xml_tag_map = {
            'condition': 'condition', 'response': 'response',
            'exclusion': 'exclude', 'inclusion': 'include', 'milestone': 'milestone'
        }
        for rel_type_plural in ['conditions', 'responses', 'coresponces', 'excludes', 'includes', 'milestones', 'updates', 'spawns']:
            if constraints_el.find(rel_type_plural) is None:
                ET.SubElement(constraints_el, rel_type_plural)

        for rel in self.dcr_data.relations:
            rel_type = rel['type']
            source_id, target_id = rel['source_id'], rel['target_id']
            xml_tag_name = relation_xml_tag_map.get(rel_type)

            parent_group_name = f"{rel_type}s"
            if rel_type == "exclusion":
                parent_group_name = "excludes"
            elif rel_type == "inclusion":
                parent_group_name = "includes"

            parent_group_el = constraints_el.find(parent_group_name)
            if xml_tag_name and parent_group_el is not None:
                rel_el = ET.SubElement(
                    parent_group_el, xml_tag_name, sourceId=source_id, targetId=target_id)
                custom_el = ET.SubElement(rel_el, "custom")
                ET.SubElement(custom_el, "waypoints")
                ET.SubElement(custom_el, "id", id=self._generate_relation_id())
            else:
                pass

    def _add_runtime(self, dcrgraph_el: ET.Element):
        runtime_el = ET.SubElement(dcrgraph_el, "runtime")
        marking_el = ET.SubElement(runtime_el, "marking")
        ET.SubElement(marking_el, "globalStore")
        executed_el = ET.SubElement(marking_el, "executed")
        included_el = ET.SubElement(marking_el, "included")
        pending_responses_el = ET.SubElement(marking_el, "pendingResponses")

        for event_id, event_data in self.dcr_data.events.items():
            initial_marking = event_data.get('initial_marking', set())
            if 'e' in initial_marking:
                ET.SubElement(executed_el, "event", id=event_id)
            if 'i' in initial_marking:
                ET.SubElement(included_el, "event", id=event_id)
            if 'p' in initial_marking:
                ET.SubElement(pending_responses_el, "event", id=event_id)


class Bpmn2DcrConverter:
    def __init__(self, temp_bpmn_file_path=None, temp_dcr_file_path=None):
        self.is_temp_files_managed_by_instance = False
        if temp_bpmn_file_path:
            self.temp_bpmn_file_path = temp_bpmn_file_path
        else:
            try:
                fd_bpmn, path_bpmn = tempfile.mkstemp(
                    suffix=".bpmn", prefix="bpmn2dcr_local_input_")
                os.close(fd_bpmn)
                self.temp_bpmn_file_path = path_bpmn
                self.is_temp_files_managed_by_instance = True
            except Exception:
                self.temp_bpmn_file_path = os.path.join(
                    os.getcwd(), "bpmn2dcr_fallback_input.bpmn")

        if temp_dcr_file_path:
            self.temp_dcr_file_path = temp_dcr_file_path
        else:
            try:
                fd_dcr, path_dcr = tempfile.mkstemp(
                    suffix=".xml", prefix="bpmn2dcr_local_output_")
                os.close(fd_dcr)
                self.temp_dcr_file_path = path_dcr
                self.is_temp_files_managed_by_instance = True
            except Exception:
                self.temp_dcr_file_path = os.path.join(
                    os.getcwd(), "bpmn2dcr_fallback_output.xml")

    def translate(self, bpmn_xml_content: str) -> str:
        try:
            with open(self.temp_bpmn_file_path, "w", encoding="utf-8") as f:
                f.write(bpmn_xml_content)
        except Exception as e_write_temp:
            return f"PYTHON_WRITE_TEMP_BPMN_ERROR: {str(e_write_temp)}\n{traceback.format_exc()}"

        analyzer = BPMNAnalyzer()
        try:
            load_success, load_error_msg = analyzer.load_from_xml(
                self.temp_bpmn_file_path)
            if not load_success:
                return f"PYTHON_LOAD_ERROR: Failed to load BPMN XML. {load_error_msg or ''}"
        except Exception as e_load:
            return f"PYTHON_LOAD_ERROR_UNCAUGHT: {str(e_load)}\n{traceback.format_exc()}"

        try:
            analyzer.pair_gateways()
        except Exception as e_pair:
            return f"PYTHON_PAIR_GATEWAYS_ERROR: {str(e_pair)}\n{traceback.format_exc()}"

        try:
            overall_passed, error_messages_list = analyzer.perform_precondition_checks()
            if not overall_passed:
                errors_str = "PYTHON_PRECONDITION_ERROR: BPMN precondition checks failed:\n" + \
                             "\n".join(
                                 [f"  - {msg}" for msg in error_messages_list])
                return errors_str
        except Exception as e_precheck:
            return f"PYTHON_PRECHECK_ERROR_UNCAUGHT: {str(e_precheck)}\n{traceback.format_exc()}"

        if analyzer.bpmn_data is None:
            return "PYTHON_TRANSLATOR_ERROR: BPMNData not initialized by analyzer."

        translator = BPMNTranslator(analyzer.bpmn_data)
        try:
            translator.map_bpmn_objects_to_dcr_events()
            translator.generic_relation_mapping()
            translator.exclusive_gateway_mapping()
            translator.parallel_gateway_mapping()
            translator.inclusive_gateway_mapping()
        except Exception as e_translate_rules:
            return f"PYTHON_TRANSLATION_RULES_ERROR: {str(e_translate_rules)}\n{traceback.format_exc()}"

        dcr_graph_data = translator.get_dcr_graph()
        if dcr_graph_data is None:
            return "PYTHON_TRANSLATOR_ERROR: DCRData not generated by translator."

        exporter = DCRExporter(dcr_graph_data)
        try:
            exporter.export_to_xml(self.temp_dcr_file_path)
        except Exception as e_export:
            return f"PYTHON_EXPORT_DCR_ERROR: {str(e_export)}\n{traceback.format_exc()}"

        try:
            with open(self.temp_dcr_file_path, "r", encoding="utf-8") as f_dcr:
                dcr_xml_output_str = f_dcr.read()
        except Exception as e_read_dcr:
            return f"PYTHON_READ_DCR_OUTPUT_ERROR: {str(e_read_dcr)}\n{traceback.format_exc()}"

        return dcr_xml_output_str

    def __del__(self):
        if self.is_temp_files_managed_by_instance:
            if hasattr(self, 'temp_bpmn_file_path') and self.temp_bpmn_file_path and os.path.exists(self.temp_bpmn_file_path):
                try:
                    os.remove(self.temp_bpmn_file_path)
                except OSError:
                    pass
            if hasattr(self, 'temp_dcr_file_path') and self.temp_dcr_file_path and os.path.exists(self.temp_dcr_file_path):
                try:
                    os.remove(self.temp_dcr_file_path)
                except OSError:
                    pass


def main():
    converter = Bpmn2DcrConverter()
    current_dir = os.getcwd()
    print(f"Searching for .bpmn or .xml files in: {current_dir}")

    files = [f for f in os.listdir(current_dir) if os.path.isfile(os.path.join(current_dir, f)) and
             (f.lower().endswith(".bpmn") or (f.lower().endswith(".xml") and "dcr_output" not in f.lower()))]

    if not files:
        print("No suitable .bpmn (or .xml that isn't a DCR output) files found.")
        return

    print("\nPlease select a BPMN file to convert by entering its number:")
    for i, filename in enumerate(files):
        print(f"{i + 1}. {filename}")

    while True:
        try:
            choice = int(input("Enter file number: "))
            if 1 <= choice <= len(files):
                selected_file = files[choice - 1]
                break
            else:
                print(
                    f"Invalid choice. Please enter a number between 1 and {len(files)}.")
        except ValueError:
            print("Invalid input. Please enter a number.")

    selected_file_path = os.path.join(current_dir, selected_file)
    print(f"\nSelected file: {selected_file}\nProcessing...")

    try:
        with open(selected_file_path, "r", encoding="utf-8") as f:
            bpmn_xml_content = f.read()
    except Exception as e:
        print(f"Error reading file {selected_file_path}: {e}")
        return

    dcr_xml_output_str = converter.translate(bpmn_xml_content)

    if dcr_xml_output_str.startswith("PYTHON_") or dcr_xml_output_str.startswith("TRANSLATION_PIPELINE_ERROR:"):
        print("\nConversion failed with the following error:")
        print(dcr_xml_output_str)
    else:
        base, _ = os.path.splitext(selected_file)
        output_filename = f"{base}_dcr_output.xml"
        output_file_path = os.path.join(current_dir, output_filename)
        try:
            with open(output_file_path, "w", encoding="utf-8") as f:
                f.write(dcr_xml_output_str)
            print(
                f"\nConversion successful!\nDCR XML output saved to: {output_file_path}")
        except Exception as e:
            print(f"\nError writing output file {output_file_path}: {e}")
            print("\nConverted DCR XML (displaying due to save error):\n",
                  dcr_xml_output_str)


if __name__ == "__main__":
    main()
