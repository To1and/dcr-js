import { executeS, isAcceptingS, isEnabledS } from "./executionEngine";
import { DCRGraphS, Event, EventMap, FuzzyRelation, RelationViolations, RoleTrace } from "./types";
import { copyEventMap, copyMarking, copySet, reverseRelation } from "./utility";

export const replayTraceS = (graph: DCRGraphS, trace: RoleTrace): boolean => {
    let retval = false;

    if (trace.length === 0) return isAcceptingS(graph, graph);

    const [head, ...tail] = trace;
    // Open world principle!
    if (!graph.labels.has(head.activity)) {
        return replayTraceS(graph, tail);
    }

    const initMarking = copyMarking(graph.marking);
    for (const event of graph.labelMapInv[head.activity]) {
        if (!(head.role === graph.roleMap[event])) continue;
        const group = graph.subProcessMap[event] ? graph.subProcessMap[event] : graph;
        if (isEnabledS(event, graph, group).enabled) {
            executeS(event, graph);
            retval = retval || replayTraceS(graph, tail);
            graph.marking = copyMarking(initMarking);
        }
    }

    return retval;
};


const mergeFuzRels = (viols1: FuzzyRelation, viols2: FuzzyRelation): FuzzyRelation => {
    const retval: FuzzyRelation = { ...viols1 };
    for (const e1 in viols2) {
        if (e1 in retval) {
            retval[e1] = Object.entries(viols2[e1]).reduce((acc, [key, value]) =>
                // if key is already in retval, add the values, otherwise, create new pair
                ({ ...acc, [key]: (acc[key] || 0) + value })
                , retval[e1]);
        } else {
            retval[e1] = { ...viols2[e1] };
        }
    }
    return retval;
}

export const mergeViolations = (viols1: RelationViolations, viols2: RelationViolations): RelationViolations => {
    return {
        conditionsFor: mergeFuzRels(viols1.conditionsFor, viols2.conditionsFor),
        responseTo: mergeFuzRels(viols1.responseTo, viols2.responseTo),
        excludesTo: mergeFuzRels(viols1.excludesTo, viols2.excludesTo),
        milestonesFor: mergeFuzRels(viols1.milestonesFor, viols2.milestonesFor),
    }
}

const emptyFuzzyRel = (events: Set<Event>): FuzzyRelation => {
    const retval: FuzzyRelation = {};
    for (const event of events) {
        retval[event] = {};
        for (const event2 of events) {
            retval[event][event2] = 0;
        }
    }
    return retval;
}

const emptyEventMap = (events: Set<Event>): EventMap => {
    const retval: EventMap = {};
    for (const event of events) {
        retval[event] = new Set();
    }
    return retval;
}

const computeActivations = (executedEvent: Event, events: Set<Event>, rel: EventMap): FuzzyRelation => {
    const retval: FuzzyRelation = {};
    for (const event of events) {
        retval[event] = {};
        if (event === executedEvent && rel[event]) {
            for (const event2 of events) {
                retval[event][event2] = rel[event].has(event2) ? 1 : 0;
            }
        } else {
            for (const event2 of events) {
                retval[event][event2] = 0;
            }
        }

    }
    return retval;
}

export const quantifyViolations = (graph: DCRGraphS, trace: RoleTrace): { totalViolations: number, violations: RelationViolations, activations: RelationViolations } => {
    // Copies and flips excludesTo and responseTo to easily find all events that are the sources of the relations
    const excludesFor = reverseRelation(graph.excludesTo);
    const responseFor = reverseRelation(graph.responseTo);

    const quantifyRec = (graph: DCRGraphS, trace: RoleTrace, exSinceIn: EventMap, exSinceEx: EventMap): { totalViolations: number, violations: RelationViolations, activations: RelationViolations } => {
        if (trace.length === 0) {
            // Response violations (each included pending event is a violation)
            // For all pending events (that are included according to the initial graph), event, at the end of a trace, all relations
            // s.t. otherEvent *-> event, where otherEvent has been executed
            // after event was last executed covers the trace
            const responseTo = emptyFuzzyRel(graph.events);
            let totalViolations = 0;
            for (const event of copySet(graph.marking.pending).intersect(
                graph.marking.included
            )) {
                for (const otherEvent of copySet(responseFor[event]).intersect(
                    exSinceEx[event]
                )) {
                    responseTo[otherEvent][event]++;
                    totalViolations++;
                }
            }
            return {
                totalViolations,
                violations: {
                    conditionsFor: emptyFuzzyRel(graph.events),
                    responseTo,
                    excludesTo: emptyFuzzyRel(graph.events),
                    milestonesFor: emptyFuzzyRel(graph.events),
                },
                activations: {
                    conditionsFor: emptyFuzzyRel(graph.events),
                    responseTo: emptyFuzzyRel(graph.events),
                    excludesTo: emptyFuzzyRel(graph.events),
                    milestonesFor: emptyFuzzyRel(graph.events),
                },
            }
        };

        const [head, ...tail] = trace;

        let leastViolations = Infinity;
        let bestRelationViolations: RelationViolations = {
            conditionsFor: {},
            responseTo: {},
            excludesTo: {},
            milestonesFor: {}
        };
        let bestRelationActivations: RelationViolations = {
            conditionsFor: {},
            responseTo: {},
            excludesTo: {},
            milestonesFor: {}
        };
        const initMarking = copyMarking(graph.marking);
        for (const event of graph.labelMapInv[head.activity]) {
            if (!(head.role === graph.roleMap[event])) continue;

            const localExSinceIn = copyEventMap(exSinceIn);
            const localExSinceEx = copyEventMap(exSinceEx);
            let localViolationCount = 0;
            const localViolations: RelationViolations = {
                conditionsFor: emptyFuzzyRel(graph.events),
                responseTo: emptyFuzzyRel(graph.events),
                excludesTo: emptyFuzzyRel(graph.events),
                milestonesFor: emptyFuzzyRel(graph.events)
            };


            const localActivations: RelationViolations = {
                conditionsFor: computeActivations(event, graph.events, graph.conditionsFor),
                responseTo: computeActivations(event, graph.events, graph.responseTo),
                excludesTo: computeActivations(event, graph.events, graph.excludesTo),
                milestonesFor: computeActivations(event, graph.events, graph.milestonesFor)
            };

            // Condition violations
            for (const otherEvent of copySet(graph.conditionsFor[event]).difference(
                graph.marking.executed,
            )) {
                if (graph.marking.included.has(otherEvent)) {
                    if (!localViolations.conditionsFor[event]) localViolations.conditionsFor[event] = {};
                    if (!localViolations.conditionsFor[event][otherEvent]) localViolations.conditionsFor[event][otherEvent] = 0;
                    localViolations.conditionsFor[event][otherEvent]++;
                    localViolationCount++;
                }
            }
            // Milestone violations
            for (const otherEvent of copySet(graph.milestonesFor[event]).intersect(
                graph.marking.pending,
            )) {
                if (graph.marking.included.has(otherEvent)) {
                    if (!localViolations.milestonesFor[event]) localViolations.milestonesFor[event] = {};
                    if (!localViolations.milestonesFor[event][otherEvent]) localViolations.milestonesFor[event][otherEvent] = 0;
                    localViolations.milestonesFor[event][otherEvent]++;
                    localViolationCount++;
                }
            }
            // Exclude violation
            // If event is not included, then for all events, 'otherEvent' that has been executed since 'event'
            // was last included, the relation otherEvent ->% event covers the trace
            if (!graph.marking.included.has(event)) {
                for (const otherEvent of copySet(localExSinceIn[event]).intersect(
                    excludesFor[event]
                )) {
                    localViolations.excludesTo[otherEvent][event]++;
                    localViolationCount++;
                }
            }

            executeS(event, graph);

            // For all events included by 'event' clear executed since included set
            for (const otherEvent of graph.includesTo[event]) {
                localExSinceIn[otherEvent] = new Set();
            }

            // Add to executed since included for all events
            for (const otherEvent of graph.events) {
                localExSinceEx[otherEvent].add(event);
                localExSinceIn[otherEvent].add(event);
            }
            // Clear executed since set
            localExSinceEx[event] = new Set([event]);

            const { totalViolations: recTotalViolations, violations: recViolations, activations: recActivations } = quantifyRec(graph, tail, localExSinceIn, localExSinceEx);
            if (localViolationCount + recTotalViolations < leastViolations) {
                leastViolations = localViolationCount + recTotalViolations;
                bestRelationViolations = mergeViolations(localViolations, recViolations);
                bestRelationActivations = mergeViolations(localActivations, recActivations);
            }
            graph.marking = copyMarking(initMarking);
        }


        graph.marking = copyMarking(initMarking);
        return { totalViolations: leastViolations, violations: bestRelationViolations, activations: bestRelationActivations };
    };

    const results = quantifyRec(graph, trace, emptyEventMap(graph.events), emptyEventMap(graph.events));

    return results;
}