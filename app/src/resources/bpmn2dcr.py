import xml.etree.ElementTree as ET
from xml.dom import minidom
from collections import deque
import os
import traceback
import hashlib


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
            if flow and flow.get('target_ref'):
                successors.append(flow['target_ref'])
        return successors

    def get_predecessors(self, element_id):
        element = self.get_element(element_id)
        if not element or 'incoming_flow_ids' not in element:
            return []
        predecessors = []
        for flow_id in element['incoming_flow_ids']:
            flow = self.get_sequence_flow(flow_id)
            if flow and flow.get('source_ref'):
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

        found_bpmn_ns = False
        for uri in self._xml_namespaces.values():
            if uri == bpmn_ns_uri:
                found_bpmn_ns = True
                break
        if not found_bpmn_ns:
            if '' not in self._xml_namespaces and 'bpmn' not in self._xml_namespaces:
                self._xml_namespaces['bpmn'] = bpmn_ns_uri

        process_tag_str = self._get_element_tag('process')
        process_element = root.find(
            process_tag_str, self._xml_namespaces if '' not in self._xml_namespaces else None)

        if process_element is None:
            process_element = root.find(process_tag_str)
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
                            child_stripped_tag = child_stripped_tag.split('}', 1)[
                                1]
                        if child_stripped_tag == stripped_tag:
                            process_element = child

                            break
                    if process_element is None:
                        return False, f"<process> element not found (tried tag: {process_tag_str}). Check XML structure/namespaces. Available NS: {self._xml_namespaces}"

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
                elements_found = process_element.findall(elem_tag)

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
            flows_found = process_element.findall(flow_tag)

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
                condition_xml = xml_flow.find(condition_expr_tag)

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

            q = deque([(branch_start_node_id, [branch_start_node_id])])

            visited_in_branch_exploration = {s_gw_id, branch_start_node_id}
            path_reaches_join_for_this_branch = False
            current_branch_path_nodes = set()

            path_found_q = deque()

            if branch_start_node_id == j_gw_id:
                path_reaches_join_for_this_branch = True
                path_found_q.append([branch_start_node_id])
            else:
                q.append((branch_start_node_id, [branch_start_node_id]))
                current_branch_path_nodes.add(branch_start_node_id)

            processed_in_branch = {s_gw_id}

            while (q):
                curr_node_id, path = q.popleft()
                processed_in_branch.add(curr_node_id)

                if curr_node_id == j_gw_id:
                    path_reaches_join_for_this_branch = True
                    path_found_q.append(path)

                    all_intermediate_nodes_in_region.update(
                        p_node for p_node in path[:-1] if p_node != s_gw_id)
                    continue

                curr_node_element = self.bpmn_data.get_element(curr_node_id)
                if curr_node_element['type'].endswith('Gateway') and \
                   curr_node_element.get('gateway_type') == s_gw.get('gateway_type') and \
                   curr_node_element.get('paired_gateway_id') is None and \
                   curr_node_id != s_gw_id and curr_node_id != j_gw_id and \
                   curr_node_element.get('gateway_direction') in ['split', 'join']:
                    all_paths_reach_join_cleanly = False
                    break

                if curr_node_id not in {s_gw_id, j_gw_id}:
                    current_branch_path_nodes.add(curr_node_id)

                for succ_id in self.bpmn_data.get_successors(curr_node_id):
                    if succ_id not in processed_in_branch:
                        if succ_id not in path:
                            q.append((succ_id, path + [succ_id]))

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

    def pair_gateways(self):
        if not self.bpmn_data:
            return
        self._determine_gateway_directions()

        all_gateways = [el for el_id, el in self.bpmn_data.elements.items(
        ) if el['type'].endswith('Gateway')]

        for gw_element in all_gateways:
            gw_element.setdefault('loop_type', None)

        paired_in_pass_loop = True
        while paired_in_pass_loop:
            paired_in_pass_loop = False
            candidate_loops = []

            unpaired_exclusive_joins = [
                gw for gw in all_gateways
                if gw.get('gateway_type') == 'exclusive' and gw.get('gateway_direction') == 'join' and gw.get('paired_gateway_id') is None
            ]
            unpaired_exclusive_splits = [
                gw for gw in all_gateways
                if gw.get('gateway_type') == 'exclusive' and gw.get('gateway_direction') == 'split' and gw.get('paired_gateway_id') is None
            ]

            for j_gw_loop in unpaired_exclusive_joins:
                for s_gw_loop in unpaired_exclusive_splits:
                    if j_gw_loop['id'] == s_gw_loop['id']:
                        continue

                    is_loop, body_nodes = self._check_loop_pairing_candidate(
                        j_gw_loop['id'], s_gw_loop['id'])
                    if is_loop:

                        contains_unpaired_gw_in_body = any(
                            (el_body := self.bpmn_data.get_element(node_id_body)) and
                            el_body['type'].endswith('Gateway') and
                            el_body.get('paired_gateway_id') is None and
                            el_body.get('gateway_direction') in [
                                'split', 'join']
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
                    paired_in_pass_loop = True

        paired_in_pass_sese = True
        while paired_in_pass_sese:
            paired_in_pass_sese = False
            candidate_sese_pairs = []

            unpaired_splits = [
                gw for gw in all_gateways
                if gw.get('gateway_direction') == 'split' and gw.get('paired_gateway_id') is None and gw.get('loop_type') is None
            ]

            for s_gw_sese in unpaired_splits:
                potential_joins = [
                    gw for gw in all_gateways
                    if gw.get('gateway_direction') == 'join' and
                    gw.get('paired_gateway_id') is None and
                    gw.get('loop_type') is None and
                    gw.get('gateway_type') == s_gw_sese.get('gateway_type')
                ]
                for j_gw_sese in potential_joins:
                    if s_gw_sese['id'] == j_gw_sese['id']:
                        continue

                    paths_valid_to_join, region_nodes, region_is_clean = self._get_region_nodes_and_validate_paths(
                        s_gw_sese['id'], j_gw_sese['id'])

                    if paths_valid_to_join and region_is_clean:
                        candidate_sese_pairs.append(
                            (s_gw_sese['id'], j_gw_sese['id'], len(region_nodes)))

            if candidate_sese_pairs:
                candidate_sese_pairs.sort(key=lambda x: x[2])
                best_s_id_sese, best_j_id_sese, _ = candidate_sese_pairs[0]

                if self.bpmn_data.elements[best_s_id_sese]['paired_gateway_id'] is None and \
                   self.bpmn_data.elements[best_j_id_sese]['paired_gateway_id'] is None:

                    self.bpmn_data.elements[best_s_id_sese]['paired_gateway_id'] = best_j_id_sese
                    self.bpmn_data.elements[best_j_id_sese]['paired_gateway_id'] = best_s_id_sese

                    paired_in_pass_sese = True

        self._populate_gateway_markings()

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
            return False, ["BPMN data not loaded. Cannot perform precondition checks."]

        error_messages = []
        overall_passed = True

        start_events = [
            e for e_id, e in self.bpmn_data.elements.items() if e['type'] == 'startEvent']
        if len(start_events) != 1:
            overall_passed = False
            msg = f"Expected exactly 1 Start Event, but found {len(start_events)}. [cite: 1]"
            error_messages.append(msg)
            for e_idx, e_item in enumerate(start_events):
                error_messages.append(
                    f"  - Start Event {e_idx+1} details: ID '{e_item['id']}', Name: '{e_item.get('name', 'N/A')}'")

        end_events = [
            e for e_id, e in self.bpmn_data.elements.items() if e['type'] == 'endEvent']
        if not end_events:
            overall_passed = False
            error_messages.append(
                "Expected at least 1 End Event, but found 0. [cite: 1]")

        gateways = [el for el_id, el in self.bpmn_data.elements.items(
        ) if el['type'].endswith('Gateway')]
        unpaired_structural_gateways_details = []
        sese_violation_details_from_direction = []

        for gw_check in gateways:
            direction = gw_check.get('gateway_direction')
            num_in = len(gw_check.get('incoming_flow_ids', []))
            num_out = len(gw_check.get('outgoing_flow_ids', []))

            is_structural_for_pairing = gw_check.get('gateway_type') in ['parallel', 'exclusive', 'inclusive'] and \
                direction in ['split', 'join']

            if is_structural_for_pairing and gw_check.get('paired_gateway_id') is None:

                if gw_check.get('loop_type') is None:
                    overall_passed = False
                    unpaired_structural_gateways_details.append(
                        f"  - ID: {gw_check['id']}, Name: {gw_check.get('name') or gw_check['id']}, Type: {gw_check.get('gateway_type')}, Det. Direction: {direction}. Expected to be paired (SESE/Loop). [Pre-condition Violation]"
                    )

            reason_sese_violation = ""
            if direction == 'split' and not (num_in == 1 and num_out > 1):
                reason_sese_violation = f"As 'split', should have 1 IN and >1 OUT. Actual - IN: {num_in}, OUT: {num_out}. [cite: 8]"
            elif direction == 'join' and not (num_in > 1 and num_out == 1):
                reason_sese_violation = f"As 'join', should have >1 IN and 1 OUT. Actual - IN: {num_in}, OUT: {num_out}. [cite: 8]"
            elif direction == 'routing_decision_point':

                if gw_check.get('gateway_type') in ['parallel', 'inclusive']:
                    reason_sese_violation = f"As '{direction}' (1-in, 1-out), this is unusual for {gw_check.get('gateway_type')} gateway type. [SESE Adherence Questionable]"
            elif direction == 'undefined_or_complex':
                reason_sese_violation = f"Has an 'undefined_or_complex' flow structure (e.g. multiple IN and OUT, or isolated). IN: {num_in}, OUT: {num_out}."

            if reason_sese_violation:

                if (is_structural_for_pairing and gw_check.get('paired_gateway_id') is None and gw_check.get('loop_type') is None) or direction == 'undefined_or_complex':
                    overall_passed = False
                    sese_violation_details_from_direction.append(
                        f"  - ID: {gw_check['id']}, Name: {gw_check.get('name') or gw_check['id']}, Type: {gw_check.get('gateway_type')}, Det. Direction: {direction or 'N/A'}. Reason: {reason_sese_violation}"
                    )

        if unpaired_structural_gateways_details:
            error_messages.append(
                "Unpaired structural gateways (should form SESE blocks or loops):")
            error_messages.extend(unpaired_structural_gateways_details)

        if sese_violation_details_from_direction:
            error_messages.append(
                "Gateways potentially violating SESE principles based on flow counts/direction:")
            error_messages.extend(sese_violation_details_from_direction)

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

                    suffix = f" Pair{pair_counter}"

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

        for mark in bpmn_markings:
            if mark.get('type') in ["S+", "S-", "J+", "J-"]:
                return False
        return True

    def _get_or_create_helper_dcr_event(self, base_id_context: str, helper_type_prefix: str, dcr_label_text: str, dcr_initial_markings: set, unique_part_for_id=""):

        if not isinstance(unique_part_for_id, str):
            unique_part_for_id = str(unique_part_for_id)

        final_dcr_label = dcr_label_text

        if helper_type_prefix == "x_expr":

            expr_hash = hashlib.md5(
                unique_part_for_id.encode()).hexdigest()[:8]
            event_dcr_id = f"expr_{base_id_context}_{expr_hash}"
            final_dcr_label = unique_part_for_id
        else:
            event_dcr_id = f"{helper_type_prefix}_{base_id_context}{'_' + unique_part_for_id if unique_part_for_id else ''}"

        if event_dcr_id not in self.dcr_data.events:
            try:
                self.dcr_data.add_event(
                    event_dcr_id, final_dcr_label, dcr_initial_markings)

                self.dcr_data.add_relation(
                    event_dcr_id, event_dcr_id, 'exclusion')
            except ValueError as e:

                return None
        return event_dcr_id

    def _get_or_create_parallel_state_event(self, associated_gpj_bpmn_id: str) -> str:

        return self._get_or_create_helper_dcr_event(associated_gpj_bpmn_id, "l_state_for_gpj", "Parallel State", {'i'})

    def _get_or_create_inclusive_state_event(self, associated_gij_bpmn_id: str) -> str:

        return self._get_or_create_helper_dcr_event(associated_gij_bpmn_id, "n_state_for_gij", "Inclusive State", set())

    def _get_or_create_expression_event(self, expression_text: str, associated_flow_bpmn_id: str) -> str:

        return self._get_or_create_helper_dcr_event(associated_flow_bpmn_id, "x_expr", expression_text, set(), unique_part_for_id=expression_text)

    def map_bpmn_objects_to_dcr_events(self):
        if not self.bpmn_data or not self.bpmn_data.elements:

            return

        for bpmn_id, bpmn_element in self.bpmn_data.elements.items():

            dcr_event_id = bpmn_id
            final_label = ""
            base_label = ""
            initial_marking = set()
            element_type = bpmn_element.get('type')

            if element_type == 'startEvent':
                base_label = 'Start Event'
                initial_marking = {'p', 'i'}
            elif element_type == 'endEvent':
                base_label = 'End Event'
                initial_marking = set()
            elif bpmn_element.get('base_type') == 'task':
                base_label = bpmn_element.get(
                    'name') or bpmn_element.get('label') or dcr_event_id
                initial_marking = set()
            elif element_type and element_type.endswith('Gateway'):

                gw_specific_type = bpmn_element.get('gateway_type', '')
                gw_direction = bpmn_element.get('gateway_direction', '')

                gw_specific_type_capitalized = gw_specific_type.capitalize(
                ) if gw_specific_type else "Gateway"
                gw_direction_capitalized = gw_direction.capitalize() if gw_direction else ""

                if gw_direction_capitalized in ['Split', 'Join']:
                    base_label = f"{gw_specific_type_capitalized} {gw_direction_capitalized}"
                elif gw_specific_type_capitalized != "Gateway":
                    base_label = f"{gw_specific_type_capitalized} Gateway"
                else:
                    base_label = "Gateway"
                initial_marking = set()

                pair_suffix = self.gateway_pair_suffixes.get(bpmn_id, "")
                final_label = f"{base_label}{pair_suffix}"
            else:

                continue

            if not final_label:
                final_label = base_label

            try:
                self.dcr_data.add_event(
                    dcr_event_id, final_label, initial_marking)

                if bpmn_id not in self.bpmn_to_dcr_event_map or self.bpmn_to_dcr_event_map[bpmn_id] != dcr_event_id:
                    self.bpmn_to_dcr_event_map[bpmn_id] = dcr_event_id

                is_self_excluded = False
                for rel in self.dcr_data.get_relations_for_event(dcr_event_id, as_source=True, as_target=True):
                    if rel['source_id'] == dcr_event_id and rel['target_id'] == dcr_event_id and rel['type'] == 'exclusion':
                        is_self_excluded = True
                        break
                if not is_self_excluded:
                    self.dcr_data.add_relation(
                        dcr_event_id, dcr_event_id, 'exclusion')

            except ValueError as e:
                pass

    def generic_relation_mapping(self):
        if not self.bpmn_data or not self.bpmn_data.sequence_flows:

            return

        for flow_id, bpmn_flow in self.bpmn_data.sequence_flows.items():
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

            elif self._is_common_task(source_bpmn_id) or self._is_common_task(target_bpmn_id):

                if target_dcr_id in self.dcr_data.events and source_dcr_id in self.dcr_data.events:
                    apply_response_inclusion = True

            elif source_element.get('base_type') == 'task' and target_element.get('base_type') == 'task':
                apply_response_inclusion = True

            elif source_element.get('gateway_direction') == 'join' and \
                    self._has_bpmn_marking(target_bpmn_id, "J+", associated_gateway_id=source_bpmn_id):
                apply_response_inclusion = True

            elif target_element.get('gateway_direction') == 'split' and \
                    self._has_bpmn_marking(source_bpmn_id, "S-", associated_gateway_id=target_bpmn_id):
                apply_response_inclusion = True

            elif source_element.get('gateway_direction') == 'split' and \
                    self._has_bpmn_marking(target_bpmn_id, "S+", associated_gateway_id=source_bpmn_id):
                apply_response_inclusion = True

            elif target_element.get('gateway_direction') == 'join' and \
                    self._has_bpmn_marking(source_bpmn_id, "J-", associated_gateway_id=target_bpmn_id):
                apply_response_inclusion = True

            if apply_response_inclusion:
                try:
                    self.dcr_data.add_relation(
                        source_dcr_id, target_dcr_id, 'response')
                    self.dcr_data.add_relation(
                        source_dcr_id, target_dcr_id, 'inclusion')
                except ValueError as e:
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

                    s_plus_successors_bpmn_ids = []
                    for flow_id_succ in bpmn_element.get('outgoing_flow_ids', []):
                        flow_succ = self.bpmn_data.get_sequence_flow(
                            flow_id_succ)
                        if flow_succ:
                            target_bpmn_id_succ = flow_succ.get('target_ref')
                            if target_bpmn_id_succ and self.bpmn_data.get_element(target_bpmn_id_succ) and \
                               self._has_bpmn_marking(target_bpmn_id_succ, "S+", associated_gateway_id=bpmn_id):
                                s_plus_successors_bpmn_ids.append(
                                    target_bpmn_id_succ)

                    s_plus_dcr_ids = [self.bpmn_to_dcr_event_map.get(
                        sid) for sid in s_plus_successors_bpmn_ids if self.bpmn_to_dcr_event_map.get(sid)]

                    for i in range(len(s_plus_dcr_ids)):
                        for j in range(i + 1, len(s_plus_dcr_ids)):
                            try:
                                self.dcr_data.add_relation(
                                    s_plus_dcr_ids[i], s_plus_dcr_ids[j], 'exclusion')
                                self.dcr_data.add_relation(
                                    s_plus_dcr_ids[j], s_plus_dcr_ids[i], 'exclusion')
                            except ValueError as e:
                                pass

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

                if bpmn_element.get('gateway_direction') == 'split':

                    if paired_gw_dcr_id:
                        try:
                            self.dcr_data.add_relation(
                                gateway_dcr_id, paired_gw_dcr_id, 'response')
                        except ValueError as e:
                            pass

                elif bpmn_element.get('gateway_direction') == 'join':

                    l_event_dcr_id = self._get_or_create_parallel_state_event(
                        bpmn_id)
                    if not l_event_dcr_id:
                        continue

                    for flow_id_inc in bpmn_element.get('incoming_flow_ids', []):
                        flow_inc = self.bpmn_data.get_sequence_flow(
                            flow_id_inc)
                        if flow_inc:
                            predecessor_bpmn_id = flow_inc.get('source_ref')
                            pred_element_details = self.bpmn_data.get_element(
                                predecessor_bpmn_id)
                            pred_dcr_id = self.bpmn_to_dcr_event_map.get(
                                predecessor_bpmn_id)

                            if pred_dcr_id and pred_element_details and \
                               self._has_bpmn_marking(predecessor_bpmn_id, "J-", associated_gateway_id=bpmn_id):
                                try:
                                    self.dcr_data.add_relation(
                                        pred_dcr_id, l_event_dcr_id, 'exclusion')
                                    self.dcr_data.add_relation(
                                        pred_dcr_id, gateway_dcr_id, 'condition')
                                    self.dcr_data.add_relation(
                                        pred_dcr_id, gateway_dcr_id, 'inclusion')
                                except ValueError as e:
                                    pass

    def inclusive_gateway_mapping(self):
        if not self.bpmn_data:
            return

        for bpmn_id, bpmn_element in self.bpmn_data.elements.items():
            if bpmn_element.get('gateway_type') == 'inclusive':
                gateway_dcr_id = self.bpmn_to_dcr_event_map.get(bpmn_id)
                if not gateway_dcr_id:
                    continue

                paired_gw_bpmn_id = bpmn_element.get('paired_gateway_id')
                paired_gw_dcr_id = self.bpmn_to_dcr_event_map.get(
                    paired_gw_bpmn_id) if paired_gw_bpmn_id else None

                if bpmn_element.get('gateway_direction') == 'split':

                    if paired_gw_dcr_id:
                        try:
                            self.dcr_data.add_relation(
                                gateway_dcr_id, paired_gw_dcr_id, 'response')
                        except ValueError as e:
                            pass

                    for flow_id_out in bpmn_element.get('outgoing_flow_ids', []):
                        flow_out = self.bpmn_data.get_sequence_flow(
                            flow_id_out)
                        if flow_out:
                            target_bpmn_id = flow_out.get('target_ref')
                            target_dcr_id = self.bpmn_to_dcr_event_map.get(
                                target_bpmn_id)
                            target_elem_details = self.bpmn_data.get_element(
                                target_bpmn_id)

                            if target_dcr_id and target_elem_details and \
                               self._has_bpmn_marking(target_bpmn_id, "S+", associated_gateway_id=bpmn_id):

                                expression_text = flow_out.get('expression')
                                if expression_text:

                                    x_event_dcr_id = self._get_or_create_expression_event(
                                        expression_text, flow_out['id'])
                                    if x_event_dcr_id:
                                        try:
                                            self.dcr_data.add_relation(
                                                x_event_dcr_id, target_dcr_id, 'response')
                                            self.dcr_data.add_relation(
                                                x_event_dcr_id, target_dcr_id, 'inclusion')
                                        except ValueError as e:
                                            pass

                elif bpmn_element.get('gateway_direction') == 'join':

                    n_event_dcr_id = self._get_or_create_inclusive_state_event(
                        bpmn_id)
                    if not n_event_dcr_id:
                        continue

                    for flow_id_in in bpmn_element.get('incoming_flow_ids', []):
                        flow_in = self.bpmn_data.get_sequence_flow(flow_id_in)
                        if flow_in:
                            predecessor_bpmn_id = flow_in.get('source_ref')
                            pred_dcr_id = self.bpmn_to_dcr_event_map.get(
                                predecessor_bpmn_id)
                            pred_element_details = self.bpmn_data.get_element(
                                predecessor_bpmn_id)

                            if pred_dcr_id and pred_element_details and \
                               self._has_bpmn_marking(predecessor_bpmn_id, "J-", associated_gateway_id=bpmn_id):
                                try:

                                    self.dcr_data.add_relation(
                                        pred_dcr_id, n_event_dcr_id, 'exclusion')

                                    self.dcr_data.add_relation(
                                        pred_dcr_id, gateway_dcr_id, 'condition')

                                    self.dcr_data.add_relation(
                                        pred_dcr_id, gateway_dcr_id, 'inclusion')
                                except ValueError as e:
                                    pass

                            expression_text_in = flow_in.get('expression')
                            if expression_text_in:
                                x_event_dcr_id_in = self._get_or_create_expression_event(
                                    expression_text_in, flow_in['id'])
                                if x_event_dcr_id_in:
                                    try:

                                        self.dcr_data.add_relation(
                                            x_event_dcr_id_in, n_event_dcr_id, 'inclusion')

                                        self.dcr_data.add_relation(
                                            gateway_dcr_id, x_event_dcr_id_in, 'exclusion')
                                    except ValueError as e:
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

        except IOError as e:
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
            'exclusion': 'exclude', 'inclusion': 'include',
            'milestone': 'milestone'
        }
        relation_group_elements = {
            rel_type: ET.SubElement(constraints_el, f"{rel_type}s" if rel_type not in [
                                    'exclude', 'include'] else f"{rel_type}s")
            for rel_type in self.dcr_data._valid_relation_types
        }

        for rel_type_str_plural in ['conditions', 'responses', 'coresponces', 'excludes', 'includes', 'milestones', 'updates', 'spawns']:
            if not constraints_el.find(rel_type_str_plural):
                ET.SubElement(constraints_el, rel_type_str_plural)

        for rel in self.dcr_data.relations:
            rel_type_from_data = rel['type']
            source_id = rel['source_id']
            target_id = rel['target_id']

            xml_tag_name = relation_xml_tag_map.get(rel_type_from_data)
            parent_group_el_name = f"{rel_type_from_data}s"
            if rel_type_from_data == "exclusion":
                parent_group_el_name = "excludes"
            if rel_type_from_data == "inclusion":
                parent_group_el_name = "includes"

            parent_group_el = constraints_el.find(parent_group_el_name)

            if xml_tag_name and parent_group_el is not None:
                rel_el = ET.SubElement(parent_group_el, xml_tag_name,
                                       sourceId=source_id, targetId=target_id)

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
            initial_marking_set = event_data.get('initial_marking', set())
            if 'e' in initial_marking_set:
                ET.SubElement(executed_el, "event", id=event_id)
            if 'i' in initial_marking_set:
                ET.SubElement(included_el, "event", id=event_id)
            if 'p' in initial_marking_set:
                ET.SubElement(pending_responses_el, "event", id=event_id)


class Bpmn2DcrConverter:
    def __init__(self):

        self.temp_bpmn_file_path = "/tmp/input.bpmn"
        self.temp_dcr_file_path = "/tmp/output.xml"

    def translate(self, bpmn_xml_content: str) -> str:
        try:

            with open(self.temp_bpmn_file_path, "w", encoding="utf-8") as f:
                f.write(bpmn_xml_content)

            analyzer = BPMNAnalyzer()
            load_success, load_error_msg = analyzer.load_from_xml(
                self.temp_bpmn_file_path)
            if not load_success:
                return f"Error: Failed to load BPMN XML. {load_error_msg or ''}"

            analyzer.pair_gateways()

            overall_passed, error_messages_list = analyzer.perform_precondition_checks()
            if not overall_passed:
                errors_str = "Error: BPMN precondition checks failed:\n" + \
                    "\n".join([f"  - {msg}" for msg in error_messages_list])
                return errors_str

            translator = BPMNTranslator(analyzer.bpmn_data)

            translator.map_bpmn_objects_to_dcr_events()

            translator.generic_relation_mapping()
            translator.exclusive_gateway_mapping()
            translator.parallel_gateway_mapping()
            translator.inclusive_gateway_mapping()

            dcr_graph_data = translator.get_dcr_graph()

            exporter = DCRExporter(dcr_graph_data)
            exporter.export_to_xml(self.temp_dcr_file_path)

            with open(self.temp_dcr_file_path, "r", encoding="utf-8") as f_dcr:
                dcr_xml_output_str = f_dcr.read()

            return dcr_xml_output_str

        except Exception as e_main:

            detailed_error = f"TRANSLATION_PIPELINE_ERROR: {str(e_main)}\n{traceback.format_exc()}"
            return detailed_error
        finally:

            if os.path.exists(self.temp_bpmn_file_path):
                try:
                    os.remove(self.temp_bpmn_file_path)
                except OSError:
                    pass
            if os.path.exists(self.temp_dcr_file_path):
                try:
                    os.remove(self.temp_dcr_file_path)
                except OSError:
                    pass
