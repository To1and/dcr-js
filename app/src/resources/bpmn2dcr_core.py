import xml.etree.ElementTree as ET
import os
from itertools import permutations, product


class BPMNParser:
    def __init__(self):
        self.bpmn_ns = {
            'bpmn': 'http://www.omg.org/spec/BPMN/20100524/MODEL',
            'bpmndi': 'http://www.omg.org/spec/BPMN/20100524/DI',
            'dc': 'http://www.omg.org/spec/DD/20100524/DC',
            'di': 'http://www.omg.org/spec/DD/20100524/DI'
        }
        self.nodes = {}
        self.edges = []
        self.cycles = []
        self.cycles_detected = False
        self.split_gateways = []
        self.merge_gateways = []
        self.gateway_pairs = {}

    def parse(self, bpmn_file):
        bpmn_tree = ET.parse(bpmn_file)
        bpmn_root = bpmn_tree.getroot()
        bpmn_process = bpmn_root.find('.//bpmn:process', self.bpmn_ns)
        if bpmn_process is None:
            raise ValueError("No BPMN process found in the input file")
        self._extract_nodes_and_edges(bpmn_process)
        self._detect_cycles()
        self._identify_gateway_structures(bpmn_process)
        return bpmn_process, bpmn_root

    def _extract_nodes_and_edges(self, bpmn_process):
        self.nodes = {}
        self.edges = []
        for node_type in ['startEvent', 'endEvent', 'task', 'parallelGateway', 'exclusiveGateway']:
            for node in bpmn_process.findall(f'.//bpmn:{node_type}', self.bpmn_ns):
                node_id = node.get('id')
                node_name = node.get('name', node_id)
                self.nodes[node_id] = {'label': node_name, 'type': node_type}
        for flow in bpmn_process.findall('.//bpmn:sequenceFlow', self.bpmn_ns):
            source_ref = flow.get('sourceRef')
            target_ref = flow.get('targetRef')
            flow_id = flow.get('id')
            condition = None
            condition_expr = flow.find(
                './/bpmn:conditionExpression', self.bpmn_ns)
            if condition_expr is not None:
                condition = condition_expr.text
            self.edges.append((source_ref, target_ref, flow_id, condition))

    def _identify_gateway_structures(self, bpmn_process):
        self.split_gateways = []
        self.merge_gateways = []
        self.gateway_pairs = {}
        all_gateways = []
        for gateway_type in ['exclusiveGateway', 'parallelGateway', 'inclusiveGateway']:
            gateways = bpmn_process.findall(
                f'.//bpmn:{gateway_type}', self.bpmn_ns)
            for gateway in gateways:
                all_gateways.append(gateway.get('id'))
        for gateway_id in all_gateways:
            outgoing_flows = self.get_outgoing_flows(bpmn_process, gateway_id)
            incoming_flows = self.get_incoming_flows(bpmn_process, gateway_id)
            if len(outgoing_flows) > 1 and len(incoming_flows) == 1:
                self.split_gateways.append(gateway_id)
            elif len(incoming_flows) > 1 and len(outgoing_flows) == 1:
                self.merge_gateways.append(gateway_id)
            elif len(outgoing_flows) > 1 and len(incoming_flows) > 1:
                self.split_gateways.append(gateway_id)
                self.merge_gateways.append(gateway_id)
        self._find_gateway_pairs(bpmn_process)

    def _find_gateway_pairs(self, bpmn_process):
        for split_id in self.split_gateways:
            outgoing_paths = []
            outgoing_flows = self.get_outgoing_flows(bpmn_process, split_id)
            for flow in outgoing_flows:
                target_id = flow.get('targetRef')
                path = self._trace_path_from_node(bpmn_process, target_id)
                outgoing_paths.append(path)
            common_nodes = set.intersection(
                *[set(path[1:]) for path in outgoing_paths]) if outgoing_paths else set()
            possible_merges = [
                node for node in common_nodes if node in self.merge_gateways]
            first_merges = []
            for path in outgoing_paths:
                for node in path:
                    if node in possible_merges:
                        first_merges.append(node)
                        break
            if first_merges and len(set(first_merges)) == 1:
                self.gateway_pairs[split_id] = first_merges[0]

    def _trace_path_from_node(self, bpmn_process, start_node, visited=None):
        if visited is None:
            visited = set()
        path = [start_node]
        visited.add(start_node)
        if self.nodes.get(start_node, {}).get('type') == 'endEvent':
            return path
        outgoing_flows = self.get_outgoing_flows(bpmn_process, start_node)
        if not outgoing_flows:
            return path
        if len(outgoing_flows) > 1 and self.nodes.get(start_node, {}).get('type') in ['exclusiveGateway', 'parallelGateway']:
            next_node = outgoing_flows[0].get('targetRef')
            if next_node not in visited:
                sub_path = self._trace_path_from_node(
                    bpmn_process, next_node, visited.copy())
                path.extend(sub_path)
        else:
            for flow in outgoing_flows:
                next_node = flow.get('targetRef')
                if next_node not in visited:
                    sub_path = self._trace_path_from_node(
                        bpmn_process, next_node, visited.copy())
                    path.extend(sub_path)
        return path

    def _detect_cycles(self):
        self.cycles = []
        graph = {}
        for source, target, _, _ in self.edges:
            if source not in graph:
                graph[source] = []
            graph[source].append(target)

        def dfs_detect_cycles(node, path, visited):
            if node in path:
                cycle_start_idx = path.index(node)
                cycle = path[cycle_start_idx:]
                has_task = any(self.nodes.get(n, {}).get(
                    'type') == 'task' for n in cycle)
                if cycle not in self.cycles and has_task:
                    task_indices = [i for i, n in enumerate(
                        cycle) if self.nodes.get(n, {}).get('type') == 'task']
                    if task_indices:
                        start_idx = task_indices[0]
                        reordered_cycle = cycle[start_idx:] + cycle[:start_idx]
                        self.cycles.append(reordered_cycle)
                    else:
                        self.cycles.append(cycle)
                return
            if node in visited:
                return
            visited.add(node)
            path.append(node)
            for neighbor in graph.get(node, []):
                dfs_detect_cycles(neighbor, path.copy(), visited)
        visited = set()
        for node in self.nodes:
            if node not in visited:
                dfs_detect_cycles(node, [], visited)
        self.cycles_detected = True

    def get_node_elements(self, bpmn_process, node_type):
        return bpmn_process.findall(f'.//bpmn:{node_type}', self.bpmn_ns)

    def get_sequence_flows(self, bpmn_process):
        return bpmn_process.findall('.//bpmn:sequenceFlow', self.bpmn_ns)

    def get_outgoing_flows(self, bpmn_process, node_id):
        return [flow for flow in self.get_sequence_flows(bpmn_process) if flow.get('sourceRef') == node_id]

    def get_incoming_flows(self, bpmn_process, node_id):
        return [flow for flow in self.get_sequence_flows(bpmn_process) if flow.get('targetRef') == node_id]

    def generate_all_traces(self, max_loop_iterations=0):
        start_event = None
        end_event = None
        for node_id, data in self.nodes.items():
            if data['type'] == 'startEvent':
                start_event = node_id
            elif data['type'] == 'endEvent':
                end_event = node_id
        if not start_event or not end_event:
            return []
        graph = {}
        for source, target, _, _ in self.edges:
            if source not in graph:
                graph[source] = []
            graph[source].append(target)
        parallel_pairs = {}
        for split_gw in self.split_gateways:
            if split_gw in self.gateway_pairs and self.nodes.get(split_gw, {}).get('type') == 'parallelGateway':
                merge_gw = self.gateway_pairs.get(split_gw)
                if merge_gw and self.nodes.get(merge_gw, {}).get('type') == 'parallelGateway':
                    parallel_pairs[split_gw] = merge_gw
        traces = []

        def extract_tasks_from_path(path):
            task_labels = []
            for node_id in path:
                node_data = self.nodes.get(node_id)
                if node_data:
                    if node_data['type'] == 'task':
                        task_labels.append(node_data['label'])
                    elif node_data['type'] == 'startEvent':
                        task_labels.append("Start Event")
                    elif node_data['type'] == 'endEvent':
                        task_labels.append("End Event")
            return task_labels

        def process_parallel_section(current_path, split_gateway, merge_gateway):
            outgoing_branches = graph.get(split_gateway, [])
            branch_paths = []
            for branch_start in outgoing_branches:
                paths = find_all_paths_between(branch_start, merge_gateway)
                branch_tasks = []
                for path in paths:
                    tasks = extract_tasks_from_path(path)
                    if tasks:
                        branch_tasks.append(tasks)
                if branch_tasks:
                    branch_paths.append(branch_tasks)
            if not branch_paths:
                return [current_path]
            combinations = list(product(*branch_paths))
            all_traces = []
            for combo in combinations:
                all_tasks = []
                for branch in combo:
                    all_tasks.extend(branch)
                interleavings = self._generate_interleavings(combo)
                for interleaving in interleavings:
                    new_trace = current_path.copy()
                    new_trace.extend(interleaving)
                    all_traces.append(new_trace)
            return all_traces

        def find_all_paths_between(start_node, end_node, visited=None, path=None):
            if visited is None:
                visited = set()
            if path is None:
                path = []
            path = path + [start_node]
            if start_node == end_node:
                return [path]
            if start_node in visited:
                return []
            visited.add(start_node)
            paths = []
            for next_node in graph.get(start_node, []):
                is_cycle_edge = False
                for cycle in self.cycles:
                    if start_node in cycle and next_node in cycle:
                        try:
                            idx1 = cycle.index(start_node)
                            idx2 = cycle.index(next_node)
                            if (idx1 + 1) % len(cycle) == idx2 or (idx1 == len(cycle) - 1 and idx2 == 0):
                                is_cycle_edge = True
                                break
                        except ValueError:
                            continue
                edge_key = (start_node, next_node)
                if is_cycle_edge and edge_key in visited and len([e for e in visited if e == edge_key]) > max_loop_iterations:
                    continue
                new_paths = find_all_paths_between(
                    next_node, end_node, visited.copy(), path)
                paths.extend(new_paths)
            return paths

        def dfs(current_node, current_path, edge_visits=None):
            if edge_visits is None:
                edge_visits = {}
            if current_node == end_event:
                current_path.append("End Event")
                traces.append(current_path)
                return
            node_type = self.nodes.get(current_node, {}).get('type')
            if node_type == 'task':
                current_path.append(self.nodes[current_node]['label'])
            elif node_type == 'startEvent':
                current_path.append("Start Event")
            successors = graph.get(current_node, [])
            if not successors:
                return
            if node_type == 'exclusiveGateway':
                for next_node in successors:
                    is_cycle_edge = False
                    for cycle in self.cycles:
                        if current_node in cycle and next_node in cycle:
                            try:
                                idx1 = cycle.index(current_node)
                                idx2 = cycle.index(next_node)
                                if (idx1 + 1) % len(cycle) == idx2 or (idx1 == len(cycle) - 1 and idx2 == 0):
                                    is_cycle_edge = True
                                    break
                            except ValueError:
                                continue
                    edge_key = (current_node, next_node)
                    edge_visits[edge_key] = edge_visits.get(edge_key, 0) + 1
                    if is_cycle_edge and edge_visits[edge_key] > max_loop_iterations + 1:
                        continue
                    dfs(next_node, current_path.copy(), edge_visits.copy())
            elif node_type == 'parallelGateway' and current_node in parallel_pairs:
                merge_gateway = parallel_pairs[current_node]
                new_traces = process_parallel_section(
                    current_path, current_node, merge_gateway)
                for trace in new_traces:
                    merge_successors = graph.get(merge_gateway, [])
                    for next_node in merge_successors:
                        dfs(next_node, trace.copy(), edge_visits.copy())
            else:
                for next_node in successors:
                    is_cycle_edge = False
                    for cycle in self.cycles:
                        if current_node in cycle and next_node in cycle:
                            try:
                                idx1 = cycle.index(current_node)
                                idx2 = cycle.index(next_node)
                                if (idx1 + 1) % len(cycle) == idx2 or (idx1 == len(cycle) - 1 and idx2 == 0):
                                    is_cycle_edge = True
                                    break
                            except ValueError:
                                continue
                    edge_key = (current_node, next_node)
                    edge_visits[edge_key] = edge_visits.get(edge_key, 0) + 1
                    if is_cycle_edge and edge_visits[edge_key] > max_loop_iterations + 1:
                        continue
                    dfs(next_node, current_path.copy(), edge_visits.copy())
        dfs(start_event, [])
        if not traces and start_event:
            traces = [["Start Event"]]
        unique_traces = []
        seen = set()
        for trace in traces:
            trace_str = ' → '.join(trace)
            if trace_str not in seen:
                seen.add(trace_str)
                unique_traces.append(trace)
        return unique_traces

    def _generate_interleavings(self, branches):
        def is_valid_interleaving(interleaving, branches):
            for branch in branches:
                branch_indices = [interleaving.index(
                    task) for task in branch if task in interleaving]
                if branch_indices != sorted(branch_indices):
                    return False
            return True
        all_tasks = []
        for branch in branches:
            all_tasks.extend(branch)
        all_permutations = list(permutations(all_tasks))
        valid_interleavings = []
        for perm in all_permutations:
            if is_valid_interleaving(perm, branches):
                valid_interleavings.append(list(perm))
        return valid_interleavings


class BPMNtoDCRMapper:
    def __init__(self):
        self.parser = BPMNParser()
        self.bpmn_ns = self.parser.bpmn_ns

    def map_elements(self, bpmn_process):
        bpmn_nodes = {}
        exclusive_gateways = []
        node_positions = {}
        self._extract_start_events(bpmn_process, bpmn_nodes)
        self._extract_tasks(bpmn_process, bpmn_nodes)
        self._extract_exclusive_gateways(
            bpmn_process, bpmn_nodes, exclusive_gateways)
        self._extract_parallel_gateways(bpmn_process, bpmn_nodes)
        self._extract_end_events(bpmn_process, bpmn_nodes)
        return bpmn_nodes, node_positions, exclusive_gateways

    def _extract_start_events(self, bpmn_process, bpmn_nodes):
        for start_event in self.parser.get_node_elements(bpmn_process, 'startEvent'):
            event_id = start_event.get('id')
            event_name = start_event.get('name', 'Start Event')
            bpmn_nodes[event_id] = {'type': 'startEvent', 'name': event_name}

    def _extract_tasks(self, bpmn_process, bpmn_nodes):
        for task in self.parser.get_node_elements(bpmn_process, 'task'):
            task_id = task.get('id')
            task_name = task.get('name', task_id)
            bpmn_nodes[task_id] = {'type': 'task', 'name': task_name}

    def _extract_exclusive_gateways(self, bpmn_process, bpmn_nodes, exclusive_gateways):
        for gateway in self.parser.get_node_elements(bpmn_process, 'exclusiveGateway'):
            gateway_id = gateway.get('id')
            gateway_name = gateway.get(
                'name', f"ExclusiveGateway_{len(exclusive_gateways)}")
            bpmn_nodes[gateway_id] = {
                'type': 'exclusiveGateway', 'name': gateway_name}
            exclusive_gateways.append(gateway_id)

    def _extract_parallel_gateways(self, bpmn_process, bpmn_nodes):
        for gateway in self.parser.get_node_elements(bpmn_process, 'parallelGateway'):
            gateway_id = gateway.get('id')
            gateway_name = gateway.get('name', f"ParallelGateway_{gateway_id}")
            bpmn_nodes[gateway_id] = {
                'type': 'parallelGateway', 'name': gateway_name}

    def _extract_end_events(self, bpmn_process, bpmn_nodes):
        for end_event in self.parser.get_node_elements(bpmn_process, 'endEvent'):
            event_id = end_event.get('id')
            event_name = end_event.get('name', 'End Event')
            bpmn_nodes[event_id] = {'type': 'endEvent', 'name': event_name}


class DCRBuilder:
    def __init__(self):
        self.parser = BPMNParser()
        self.bpmn_ns = self.parser.bpmn_ns
        self.relation_counter = 0
        self.relation_paths = {}
        self.include_offset = 0
        self.response_offset = 0
        self.exclude_offset = 0
        self.gateway_branches = {}
        self.branch_paths = {}
        self.merge_gateways = {}
        self.parallel_states = {}

    def build_dcr_graph(self, bpmn_process, bpmn_nodes, node_positions, exclusive_gateways):
        self.relation_paths = {}
        self.parallel_states = {}
        dcr_root = self._create_dcr_structure()
        specification = dcr_root.find('specification')
        resources = specification.find('resources')
        events = resources.find('events')
        labels = resources.find('labels')
        label_mappings = resources.find('labelMappings')
        constraints = specification.find('constraints')
        includes = constraints.find('includes')
        excludes = constraints.find('excludes')
        responses = constraints.find('responses')
        conditions = constraints.find('conditions')
        dcr_events = {}
        x_base = 0
        y_base = 0
        for bpmn_id, node_info in bpmn_nodes.items():
            if node_info['type'] == 'exclusiveGateway' or node_info['type'] == 'parallelGateway':
                continue
            dcr_event_id = f"Event_{len(dcr_events)}"
            dcr_event = ET.SubElement(events, 'event')
            dcr_event.set('id', dcr_event_id)
            x_pos = x_base + (len(dcr_events) * 200)
            y_pos = y_base
            self._add_event_visualization(dcr_event, x_pos, y_pos)
            self._add_label_and_mapping(
                labels, label_mappings, dcr_event_id, node_info['name'])
            dcr_events[bpmn_id] = dcr_event_id
        self._identify_gateway_structures(bpmn_process, exclusive_gateways)
        parallel_gateways = self._identify_parallel_gateways(bpmn_process)
        self._handle_exclusive_gateways(
            bpmn_process, exclusive_gateways, dcr_events, excludes, events)
        self._handle_parallel_gateways(bpmn_process, parallel_gateways, dcr_events,
                                       events, includes, responses, excludes, conditions, labels, label_mappings)
        self._process_sequence_flows(
            bpmn_process, bpmn_nodes, dcr_events, includes, responses, events)
        self._handle_cycles(bpmn_process, bpmn_nodes,
                            dcr_events, includes, excludes, responses, events)
        for bpmn_id, dcr_id in dcr_events.items():
            self._create_self_exclusion(excludes, dcr_id, events)
        self._create_runtime_section(dcr_root, dcr_events, bpmn_nodes)
        return dcr_root, dcr_events

    def _identify_parallel_gateways(self, bpmn_process):
        parallel_gateways = []
        for gateway in self.parser.get_node_elements(bpmn_process, 'parallelGateway'):
            gateway_id = gateway.get('id')
            parallel_gateways.append(gateway_id)
        return parallel_gateways

    def _identify_gateway_structures(self, bpmn_process, exclusive_gateways):
        self.gateway_branches = {}
        self.branch_paths = {}
        self.merge_gateways = {}
        for gateway_id in exclusive_gateways:
            outgoing_flows = self.parser.get_outgoing_flows(
                bpmn_process, gateway_id)
            branch_targets = [flow.get('targetRef') for flow in outgoing_flows]
            self.gateway_branches[gateway_id] = branch_targets
            branch_paths = {}
            for target in branch_targets:
                path = self._trace_path_to_merge_or_split(
                    bpmn_process, target, exclusive_gateways)
                branch_paths[target] = path
            self.branch_paths[gateway_id] = branch_paths
            potential_merges = self._find_merge_gateway(
                bpmn_process, gateway_id, branch_targets)
            if potential_merges:
                self.merge_gateways[gateway_id] = potential_merges[0]
        parallel_gateways = self._identify_parallel_gateways(bpmn_process)
        for gateway_id in parallel_gateways:
            outgoing_flows = self.parser.get_outgoing_flows(
                bpmn_process, gateway_id)
            if len(outgoing_flows) <= 1:
                continue
            branch_targets = [flow.get('targetRef') for flow in outgoing_flows]
            potential_merges = self._find_merge_gateway(
                bpmn_process, gateway_id, branch_targets, gateway_type='parallelGateway')
            if potential_merges:
                self.merge_gateways[gateway_id] = potential_merges[0]

    def _trace_path_to_merge_or_split(self, bpmn_process, start_node, exclusive_gateways):
        path = [start_node]
        current = start_node
        while True:
            outgoing_flows = self.parser.get_outgoing_flows(
                bpmn_process, current)
            if not outgoing_flows:
                break
            next_node = outgoing_flows[0].get('targetRef')
            if next_node in exclusive_gateways:
                incoming_flows = self.parser.get_incoming_flows(
                    bpmn_process, next_node)
                if len(incoming_flows) > 1:
                    path.append(next_node)
                    break
                outgoing_split = self.parser.get_outgoing_flows(
                    bpmn_process, next_node)
                if len(outgoing_split) > 1:
                    path.append(next_node)
                    break
            path.append(next_node)
            current = next_node
        return path

    def _find_merge_gateway(self, bpmn_process, split_gateway, branch_targets, gateway_type='exclusiveGateway'):
        paths = []
        for target in branch_targets:
            path = self._trace_full_path(bpmn_process, target)
            paths.append(set(path))
        common_nodes = set.intersection(*paths) if paths else set()
        potential_merges = []
        for node in common_nodes:
            gateway = bpmn_process.find(
                f'.//bpmn:{gateway_type}[@id="{node}"]', self.bpmn_ns)
            if gateway is not None:
                incoming_flows = self.parser.get_incoming_flows(
                    bpmn_process, node)
                if len(incoming_flows) > 1:
                    potential_merges.append(node)
        return potential_merges

    def _trace_full_path(self, bpmn_process, start_node):
        path = [start_node]
        visited = set([start_node])
        queue = [start_node]
        while queue:
            current = queue.pop(0)
            outgoing_flows = self.parser.get_outgoing_flows(
                bpmn_process, current)
            for flow in outgoing_flows:
                target = flow.get('targetRef')
                if target not in visited:
                    path.append(target)
                    visited.add(target)
                    queue.append(target)
        return path

    def _create_dcr_structure(self):
        dcr_root = ET.Element('dcrgraph')
        specification = ET.SubElement(dcr_root, 'specification')
        resources = ET.SubElement(specification, 'resources')
        ET.SubElement(resources, 'events')
        ET.SubElement(resources, 'labels')
        ET.SubElement(resources, 'labelMappings')
        ET.SubElement(resources, 'subProcesses')
        ET.SubElement(resources, 'variables')
        ET.SubElement(resources, 'expressions')
        variable_accesses = ET.SubElement(resources, 'variableAccesses')
        ET.SubElement(variable_accesses, 'readAccessess')
        ET.SubElement(variable_accesses, 'writeAccessess')
        constraints = ET.SubElement(specification, 'constraints')
        ET.SubElement(constraints, 'conditions')
        ET.SubElement(constraints, 'responses')
        ET.SubElement(constraints, 'coresponces')
        ET.SubElement(constraints, 'excludes')
        ET.SubElement(constraints, 'includes')
        ET.SubElement(constraints, 'milestones')
        return dcr_root

    def _add_event_visualization(self, event_element, x_pos, y_pos):
        custom = ET.SubElement(event_element, 'custom')
        visualization = ET.SubElement(custom, 'visualization')
        location = ET.SubElement(visualization, 'location')
        location.set('xLoc', str(x_pos))
        location.set('yLoc', str(y_pos))
        size = ET.SubElement(visualization, 'size')
        size.set('width', "130")
        size.set('height', "150")

    def _add_label_and_mapping(self, labels, label_mappings, event_id, label_text):
        label = ET.SubElement(labels, 'label')
        label.set('id', label_text)
        label_mapping = ET.SubElement(label_mappings, 'labelMapping')
        label_mapping.set('eventId', event_id)
        label_mapping.set('labelId', label_text)

    def _handle_cycles(self, bpmn_process, bpmn_nodes, dcr_events, includes, excludes, responses, events):
        self.parser._detect_cycles()
        for cycle in self.parser.cycles:
            cycle_nodes = [node for node in cycle if node in bpmn_nodes]
            if len(cycle_nodes) <= 1:
                continue
            cycle_dcr_events = []
            for node_id in cycle_nodes:
                if node_id in dcr_events:
                    cycle_dcr_events.append(dcr_events[node_id])
            if len(cycle_dcr_events) <= 1:
                continue
            for i, node_id in enumerate(cycle_nodes):
                if node_id not in dcr_events:
                    continue
                dcr_id = dcr_events[node_id]
                next_idx = (i + 1) % len(cycle_nodes)
                next_node_id = cycle_nodes[next_idx]
                if next_node_id not in dcr_events:
                    continue
                next_dcr_id = dcr_events[next_node_id]
                self._ensure_cycle_relations(
                    bpmn_process, node_id, next_node_id, dcr_id, next_dcr_id, includes, responses, events)
                outgoing_flows = self.parser.get_outgoing_flows(
                    bpmn_process, node_id)
                for flow in outgoing_flows:
                    target_id = flow.get('targetRef')
                    if target_id in cycle_nodes:
                        continue
                    if target_id not in dcr_events:
                        continue
                    target_dcr_id = dcr_events[target_id]
                    self._create_exclusion_relation(
                        excludes, target_dcr_id, dcr_id, events)

    def _ensure_cycle_relations(self, bpmn_process, source_id, target_id, source_dcr_id, target_dcr_id, includes, responses, events):
        direct_flow = False
        for flow in self.parser.get_sequence_flows(bpmn_process):
            if flow.get('sourceRef') == source_id and flow.get('targetRef') == target_id:
                direct_flow = True
                break
        if not direct_flow:
            include = ET.SubElement(includes, 'include')
            include.set('sourceId', source_dcr_id)
            include.set('targetId', target_dcr_id)
            inc_custom = ET.SubElement(include, 'custom')
            inc_waypoints = ET.SubElement(inc_custom, 'waypoints')
            self._add_default_waypoints(inc_waypoints)
            inc_id = ET.SubElement(inc_custom, 'id')
            relation_id = f"Relation_{self.relation_counter}"
            self.relation_counter += 1
            inc_id.set('id', relation_id)
            response = ET.SubElement(responses, 'response')
            response.set('sourceId', source_dcr_id)
            response.set('targetId', target_dcr_id)
            resp_custom = ET.SubElement(response, 'custom')
            resp_waypoints = ET.SubElement(resp_custom, 'waypoints')
            self._add_default_waypoints(resp_waypoints)
            resp_id = ET.SubElement(resp_custom, 'id')
            relation_id = f"Relation_{self.relation_counter}"
            self.relation_counter += 1
            resp_id.set('id', relation_id)

    def _handle_exclusive_gateways(self, bpmn_process, exclusive_gateways, dcr_events, excludes, events):
        gateway_groups = {}
        for gateway_id in exclusive_gateways:
            if gateway_id in self.merge_gateways.values():
                continue
            outgoing_flows = self.parser.get_outgoing_flows(
                bpmn_process, gateway_id)
            direct_targets = []
            for flow in outgoing_flows:
                target_id = flow.get('targetRef')
                if target_id in dcr_events:
                    direct_targets.append(target_id)
                else:
                    event_nodes = self._find_first_events_after_gateway(
                        bpmn_process, target_id, dcr_events)
                    direct_targets.extend(event_nodes)
            gateway_groups[gateway_id] = direct_targets
        for gateway_id, targets in gateway_groups.items():
            self._create_branch_exclusions(
                targets, dcr_events, excludes, events)
            merge_id = self.merge_gateways.get(gateway_id)
            if merge_id:
                merge_branches = []
                for flow in self.parser.get_incoming_flows(bpmn_process, merge_id):
                    source_id = flow.get('sourceRef')
                    if source_id in dcr_events:
                        merge_branches.append(source_id)
                    else:
                        event_nodes = self._find_last_events_before_gateway(
                            bpmn_process, source_id, dcr_events)
                        merge_branches.extend(event_nodes)
                self._create_branch_exclusions(
                    merge_branches, dcr_events, excludes, events)

    def _create_branch_exclusions(self, branch_nodes, dcr_events, excludes, events):
        branch_event_ids = [dcr_events[node_id]
                            for node_id in branch_nodes if node_id in dcr_events]
        for i, source_event_id in enumerate(branch_event_ids):
            for target_event_id in branch_event_ids[i+1:]:
                self._create_exclusion_relation(
                    excludes, source_event_id, target_event_id, events)
                self._create_exclusion_relation(
                    excludes, target_event_id, source_event_id, events)

    def _find_first_events_after_gateway(self, bpmn_process, gateway_id, dcr_events):
        event_nodes = []
        visited = set()

        def dfs(node_id):
            if node_id in visited:
                return
            visited.add(node_id)
            if node_id in dcr_events:
                event_nodes.append(node_id)
                return
            outgoing_flows = self.parser.get_outgoing_flows(
                bpmn_process, node_id)
            for flow in outgoing_flows:
                target_id = flow.get('targetRef')
                dfs(target_id)
        outgoing_flows = self.parser.get_outgoing_flows(
            bpmn_process, gateway_id)
        for flow in outgoing_flows:
            target_id = flow.get('targetRef')
            dfs(target_id)
        return event_nodes

    def _find_last_events_before_gateway(self, bpmn_process, gateway_id, dcr_events):
        event_nodes = []
        visited = set()

        def dfs(node_id):
            if node_id in visited:
                return
            visited.add(node_id)
            if node_id in dcr_events:
                event_nodes.append(node_id)
                return
            incoming_flows = self.parser.get_incoming_flows(
                bpmn_process, node_id)
            for flow in incoming_flows:
                source_id = flow.get('sourceRef')
                dfs(source_id)
        incoming_flows = self.parser.get_incoming_flows(
            bpmn_process, gateway_id)
        for flow in incoming_flows:
            source_id = flow.get('sourceRef')
            dfs(source_id)
        return event_nodes

    def _process_sequence_flows(self, bpmn_process, bpmn_nodes, dcr_events, includes, responses, events):
        processed_flows = set()
        self.parser._detect_cycles()
        for flow in self.parser.get_sequence_flows(bpmn_process):
            flow_id = flow.get('id')
            if flow_id in processed_flows:
                continue
            source_id = flow.get('sourceRef')
            target_id = flow.get('targetRef')
            if source_id not in bpmn_nodes or target_id not in bpmn_nodes:
                processed_flows.add(flow_id)
                continue
            is_cycle_flow = False
            for cycle in self.parser.cycles:
                if source_id in cycle and target_id in cycle:
                    try:
                        idx_source = cycle.index(source_id)
                        idx_target = cycle.index(target_id)
                        if (idx_source + 1) % len(cycle) == idx_target:
                            is_cycle_flow = True
                            break
                    except ValueError:
                        continue
            if is_cycle_flow:
                processed_flows.add(flow_id)
                continue
            source_type = bpmn_nodes.get(source_id, {}).get('type')
            if source_type in ['exclusiveGateway', 'parallelGateway']:
                processed_flows.add(flow_id)
                continue
            target_type = bpmn_nodes.get(target_id, {}).get('type')
            if target_type in ['exclusiveGateway', 'parallelGateway']:
                if target_type == 'parallelGateway':
                    processed_flows.add(flow_id)
                    continue
                outgoing_flows = self.parser.get_outgoing_flows(
                    bpmn_process, target_id)
                for gateway_flow in outgoing_flows:
                    gateway_target = gateway_flow.get('targetRef')
                    if gateway_target not in dcr_events:
                        target_events = self._find_first_events_after_gateway(
                            bpmn_process, gateway_target, dcr_events)
                        for event_target in target_events:
                            if source_id in dcr_events and event_target in dcr_events:
                                self._create_sequence_flow_relations(
                                    includes, responses, dcr_events[source_id], dcr_events[event_target], events)
                    elif source_id in dcr_events and gateway_target in dcr_events:
                        self._create_sequence_flow_relations(
                            includes, responses, dcr_events[source_id], dcr_events[gateway_target], events)
                processed_flows.add(flow_id)
                continue
            if source_id in dcr_events and target_id in dcr_events:
                self._create_sequence_flow_relations(
                    includes, responses, dcr_events[source_id], dcr_events[target_id], events)
            processed_flows.add(flow_id)

    def _handle_parallel_gateways(self, bpmn_process, parallel_gateways, dcr_events, events, includes, responses, excludes, conditions, labels, label_mappings):
        for split_gateway in parallel_gateways:
            outgoing_flows = self.parser.get_outgoing_flows(
                bpmn_process, split_gateway)
            if len(outgoing_flows) <= 1:
                continue
            merge_gateway = self.merge_gateways.get(split_gateway)
            if not merge_gateway:
                continue
            pre_node_id = None
            for flow in self.parser.get_incoming_flows(bpmn_process, split_gateway):
                pre_node_id = flow.get('sourceRef')
                break
            if pre_node_id not in dcr_events:
                continue
            pre_node_dcr_id = dcr_events[pre_node_id]
            post_node_id = None
            post_flows = self.parser.get_outgoing_flows(
                bpmn_process, merge_gateway)
            if post_flows:
                targets = []
                for flow in post_flows:
                    target = flow.get('targetRef')
                    if target in dcr_events:
                        targets.append(target)
                    else:
                        next_events = self._find_first_events_after_gateway(
                            bpmn_process, target, dcr_events)
                        targets.extend(next_events)
                if targets:
                    post_node_id = targets[0]
            branch_states = {}
            for i, flow in enumerate(outgoing_flows):
                state_id = f"ParallelState_{split_gateway}_{i}"
                state_event = ET.SubElement(events, 'event')
                state_event.set('id', state_id)
                state_event.set('role', "Non-executable")
                self._add_event_visualization(
                    state_event, 400 + (i * 150), 300 + (i * 200))
                state_name = f"Parallel State {i+1}"
                self._add_label_and_mapping(
                    labels, label_mappings, state_id, state_name)
                self._create_self_exclusion(excludes, state_id, events)
                branch_states[i] = state_id
                self.parallel_states[state_id] = state_id
            branch_start_tasks = {}
            branch_end_tasks = {}
            for i, flow in enumerate(outgoing_flows):
                branch_target = flow.get('targetRef')
                start_tasks = []
                if branch_target in dcr_events:
                    start_tasks.append(branch_target)
                else:
                    start_tasks = self._find_first_events_after_gateway(
                        bpmn_process, branch_target, dcr_events)
                if start_tasks:
                    branch_start_tasks[i] = start_tasks[0]
            for i, flow in enumerate(self.parser.get_incoming_flows(bpmn_process, merge_gateway)):
                branch_source = flow.get('sourceRef')
                end_tasks = []
                if branch_source in dcr_events:
                    end_tasks.append(branch_source)
                else:
                    end_tasks = self._find_last_events_before_gateway(
                        bpmn_process, branch_source, dcr_events)
                if end_tasks:
                    branch_end_tasks[i] = end_tasks[0]
            for i, task_id in branch_start_tasks.items():
                include = ET.SubElement(includes, 'include')
                include.set('sourceId', pre_node_dcr_id)
                include.set('targetId', dcr_events[task_id])
                inc_custom = ET.SubElement(include, 'custom')
                inc_waypoints = ET.SubElement(inc_custom, 'waypoints')
                self._add_default_waypoints(inc_waypoints)
                inc_id = ET.SubElement(inc_custom, 'id')
                relation_id = f"Relation_{self.relation_counter}"
                self.relation_counter += 1
                inc_id.set('id', relation_id)
                response = ET.SubElement(responses, 'response')
                response.set('sourceId', pre_node_dcr_id)
                response.set('targetId', dcr_events[task_id])
                resp_custom = ET.SubElement(response, 'custom')
                resp_waypoints = ET.SubElement(resp_custom, 'waypoints')
                self._add_default_waypoints(resp_waypoints)
                resp_id = ET.SubElement(resp_custom, 'id')
                relation_id = f"Relation_{self.relation_counter}"
                self.relation_counter += 1
                resp_id.set('id', relation_id)
            for i, task_id in branch_end_tasks.items():
                if i in branch_states:
                    exclude = ET.SubElement(excludes, 'exclude')
                    exclude.set('sourceId', dcr_events[task_id])
                    exclude.set('targetId', branch_states[i])
                    excl_custom = ET.SubElement(exclude, 'custom')
                    excl_waypoints = ET.SubElement(excl_custom, 'waypoints')
                    self._add_default_waypoints(excl_waypoints)
                    excl_id = ET.SubElement(excl_custom, 'id')
                    relation_id = f"Relation_{self.relation_counter}"
                    self.relation_counter += 1
                    excl_id.set('id', relation_id)
            if post_node_id and post_node_id in dcr_events:
                post_node_dcr_id = dcr_events[post_node_id]
                for task_id in branch_end_tasks.values():
                    include = ET.SubElement(includes, 'include')
                    include.set('sourceId', dcr_events[task_id])
                    include.set('targetId', post_node_dcr_id)
                    inc_custom = ET.SubElement(include, 'custom')
                    inc_waypoints = ET.SubElement(inc_custom, 'waypoints')
                    self._add_default_waypoints(inc_waypoints)
                    inc_id = ET.SubElement(inc_custom, 'id')
                    relation_id = f"Relation_{self.relation_counter}"
                    self.relation_counter += 1
                    inc_id.set('id', relation_id)
                    response = ET.SubElement(responses, 'response')
                    response.set('sourceId', dcr_events[task_id])
                    response.set('targetId', post_node_dcr_id)
                    resp_custom = ET.SubElement(response, 'custom')
                    resp_waypoints = ET.SubElement(resp_custom, 'waypoints')
                    self._add_default_waypoints(resp_waypoints)
                    resp_id = ET.SubElement(resp_custom, 'id')
                    relation_id = f"Relation_{self.relation_counter}"
                    self.relation_counter += 1
                    resp_id.set('id', relation_id)
                response = ET.SubElement(responses, 'response')
                response.set('sourceId', pre_node_dcr_id)
                response.set('targetId', post_node_dcr_id)
                resp_custom = ET.SubElement(response, 'custom')
                resp_waypoints = ET.SubElement(resp_custom, 'waypoints')
                self._add_default_waypoints(resp_waypoints)
                resp_id = ET.SubElement(resp_custom, 'id')
                relation_id = f"Relation_{self.relation_counter}"
                self.relation_counter += 1
                resp_id.set('id', relation_id)
                for state_id in branch_states.values():
                    condition = ET.SubElement(conditions, 'condition')
                    condition.set('sourceId', state_id)
                    condition.set('targetId', post_node_dcr_id)
                    cond_custom = ET.SubElement(condition, 'custom')
                    cond_waypoints = ET.SubElement(cond_custom, 'waypoints')
                    self._add_default_waypoints(cond_waypoints)
                    cond_id = ET.SubElement(cond_custom, 'id')
                    relation_id = f"Relation_{self.relation_counter}"
                    self.relation_counter += 1
                    cond_id.set('id', relation_id)
                for state_id in branch_states.values():
                    include = ET.SubElement(includes, 'include')
                    include.set('sourceId', post_node_dcr_id)
                    include.set('targetId', state_id)
                    inc_custom = ET.SubElement(include, 'custom')
                    inc_waypoints = ET.SubElement(inc_custom, 'waypoints')
                    self._add_default_waypoints(inc_waypoints)
                    inc_id = ET.SubElement(inc_custom, 'id')
                    relation_id = f"Relation_{self.relation_counter}"
                    self.relation_counter += 1
                    inc_id.set('id', relation_id)

    def _add_default_waypoints(self, waypoints_element):
        for i in range(4):
            wp = ET.SubElement(waypoints_element, 'waypoint')
            wp.set('x', str(i * 50))
            wp.set('y', str(i * 30))

    def _create_sequence_flow_relations(self, includes, responses, source_id, target_id, events):
        include = ET.SubElement(includes, 'include')
        include.set('sourceId', source_id)
        include.set('targetId', target_id)
        inc_custom = ET.SubElement(include, 'custom')
        inc_waypoints = ET.SubElement(inc_custom, 'waypoints')
        self._add_default_waypoints(inc_waypoints)
        inc_id = ET.SubElement(inc_custom, 'id')
        relation_id = f"Relation_{self.relation_counter}"
        self.relation_counter += 1
        inc_id.set('id', relation_id)
        response = ET.SubElement(responses, 'response')
        response.set('sourceId', source_id)
        response.set('targetId', target_id)
        resp_custom = ET.SubElement(response, 'custom')
        resp_waypoints = ET.SubElement(resp_custom, 'waypoints')
        self._add_default_waypoints(resp_waypoints)
        resp_id = ET.SubElement(resp_custom, 'id')
        relation_id = f"Relation_{self.relation_counter}"
        self.relation_counter += 1
        resp_id.set('id', relation_id)

    def _create_exclusion_relation(self, excludes, source_id, target_id, events):
        if source_id == target_id:
            return self._create_self_exclusion(excludes, source_id, events)
        exclude = ET.SubElement(excludes, 'exclude')
        exclude.set('sourceId', source_id)
        exclude.set('targetId', target_id)
        excl_custom = ET.SubElement(exclude, 'custom')
        excl_waypoints = ET.SubElement(excl_custom, 'waypoints')
        self._add_default_waypoints(excl_waypoints)
        excl_id = ET.SubElement(excl_custom, 'id')
        relation_id = f"Relation_{self.relation_counter}"
        self.relation_counter += 1
        excl_id.set('id', relation_id)

    def _create_self_exclusion(self, excludes, event_id, events):
        exclude = ET.SubElement(excludes, 'exclude')
        exclude.set('sourceId', event_id)
        exclude.set('targetId', event_id)
        excl_custom = ET.SubElement(exclude, 'custom')
        excl_waypoints = ET.SubElement(excl_custom, 'waypoints')
        for i in range(5):
            wp = ET.SubElement(excl_waypoints, 'waypoint')
            wp.set('x', str(i * 10))
            wp.set('y', str(i * 10))
        excl_id = ET.SubElement(excl_custom, 'id')
        relation_id = f"Relation_{self.relation_counter}"
        self.relation_counter += 1
        excl_id.set('id', relation_id)

    def _create_runtime_section(self, dcr_root, dcr_events, bpmn_nodes):
        runtime = ET.SubElement(dcr_root, 'runtime')
        marking = ET.SubElement(runtime, 'marking')
        ET.SubElement(marking, 'globalStore')
        ET.SubElement(marking, 'executed')
        included = ET.SubElement(marking, 'included')
        pending_responses = ET.SubElement(marking, 'pendingResponses')
        for bpmn_id, dcr_id in dcr_events.items():
            if bpmn_nodes[bpmn_id]['type'] == 'startEvent':
                event_el = ET.SubElement(included, 'event')
                event_el.set('id', dcr_id)
                event_pr = ET.SubElement(pending_responses, 'event')
                event_pr.set('id', dcr_id)
        for state_id in self.parallel_states:
            event_el = ET.SubElement(included, 'event')
            event_el.set('id', state_id)


class DCRWriter:
    def __init__(self):
        pass

    def write_dcr_file(self, dcr_root, output_file):
        self._indent_xml(dcr_root)
        tree = ET.ElementTree(dcr_root)
        with open(output_file, 'wb') as f:
            f.write(b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n')
            tree.write(f, encoding='utf-8', xml_declaration=False)
        return output_file

    def _indent_xml(self, elem, level=0):
        i = "\n" + level * "  "
        if len(elem):
            if not elem.text or not elem.text.strip():
                elem.text = i + "  "
            if not elem.tail or not elem.tail.strip():
                elem.tail = i
            for child in list(elem):
                self._indent_xml(child, level + 1)
            if not elem.tail or not elem.tail.strip():
                elem.tail = i
        else:
            if level and (not elem.tail or not elem.tail.strip()):
                elem.tail = i


class Translator:
    def __init__(self):
        self.parser = BPMNParser()
        self.mapper = BPMNtoDCRMapper()
        self.builder = DCRBuilder()
        self.writer = DCRWriter()

    def translate_bpmn_to_dcr(self, bpmn_file, output_file=None):
        if output_file is None:
            output_file = os.path.splitext(bpmn_file)[0] + ".xml"
        bpmn_process, bpmn_root = self.parser.parse(bpmn_file)
        bpmn_nodes, node_positions, exclusive_gateways = self.mapper.map_elements(
            bpmn_process)
        dcr_root, dcr_events = self.builder.build_dcr_graph(
            bpmn_process, bpmn_nodes, node_positions, exclusive_gateways)
        self.writer.write_dcr_file(dcr_root, output_file)
        return output_file
