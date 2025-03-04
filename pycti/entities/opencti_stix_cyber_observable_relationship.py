# coding: utf-8


class StixCyberObservableRelationship:
    def __init__(self, opencti):
        self.opencti = opencti
        self.properties = """
            id
            entity_type
            parent_types
            spec_version
            created_at
            updated_at
            standard_id
            relationship_type
            start_time
            stop_time
            from {
                ... on StixCyberObservable {
                    id
                    standard_id
                    entity_type
                    parent_types
                    observable_value
                }
            }
            to {
                ... on StixCyberObservable {
                    id
                    standard_id
                    entity_type
                    parent_types
                    observable_value
                }
            }
        """

    """
        List stix_observable_relationship objects

        :param fromId: the id of the source entity of the relation
        :param toId: the id of the target entity of the relation
        :param relationship_type: the relation type
        :param startTimeStart: the first_seen date start filter
        :param startTimeStop: the first_seen date stop filter
        :param stopTimeStart: the last_seen date start filter
        :param stopTimeStop: the last_seen date stop filter
        :param first: return the first n rows from the after ID (or the beginning if not set)
        :param after: ID of the first row for pagination
        :return List of stix_observable_relationship objects
    """

    def list(self, **kwargs):
        element_id = kwargs.get("elementId", None)
        from_id = kwargs.get("fromId", None)
        from_types = kwargs.get("fromTypes", None)
        to_id = kwargs.get("toId", None)
        to_types = kwargs.get("toTypes", None)
        relationship_type = kwargs.get("relationship_type", None)
        start_time_start = kwargs.get("startTimeStart", None)
        start_time_stop = kwargs.get("startTimeStop", None)
        stop_time_start = kwargs.get("stopTimeStart", None)
        stop_time_stop = kwargs.get("stopTimeStop", None)
        filters = kwargs.get("filters", [])
        first = kwargs.get("first", 500)
        after = kwargs.get("after", None)
        order_by = kwargs.get("orderBy", None)
        order_mode = kwargs.get("orderMode", None)
        custom_attributes = kwargs.get("customAttributes", None)
        get_all = kwargs.get("getAll", False)
        with_pagination = kwargs.get("withPagination", False)
        if get_all:
            first = 500

        self.opencti.log(
            "info",
            "Listing stix_observable_relationships with {type: "
            + str(relationship_type)
            + ", from_id: "
            + str(from_id)
            + ", to_id: "
            + str(to_id)
            + "}",
        )
        query = (
            """
            query StixCyberObservableRelationships($elementId: String, $fromId: String, $fromTypes: [String], $toId: String, $toTypes: [String], $relationship_type: [String], $startTimeStart: DateTime, $startTimeStop: DateTime, $stopTimeStart: DateTime, $stopTimeStop: DateTime, $filters: [StixCyberObservableRelationshipsFiltering], $first: Int, $after: ID, $orderBy: StixCyberObservableRelationshipsOrdering, $orderMode: OrderingMode) {
                stixCyberObservableRelationships(elementId: $elementId, fromId: $fromId, fromTypes: $fromTypes, toId: $toId, toTypes: $toTypes, relationship_type: $relationship_type, startTimeStart: $startTimeStart, startTimeStop: $startTimeStop, stopTimeStart: $stopTimeStart, stopTimeStop: $stopTimeStop, filters: $filters, first: $first, after: $after, orderBy: $orderBy, orderMode: $orderMode) {
                    edges {
                        node {
                            """
            + (custom_attributes if custom_attributes is not None else self.properties)
            + """
                        }
                    }
                    pageInfo {
                        startCursor
                        endCursor
                        hasNextPage
                        hasPreviousPage
                        globalCount
                    }
                }
            }
         """
        )

        result = self.opencti.query(
            query,
            {
                "elementId": element_id,
                "fromId": from_id,
                "fromTypes": from_types,
                "toId": to_id,
                "toTypes": to_types,
                "relationship_type": relationship_type,
                "startTimeStart": start_time_start,
                "startTimeStop": start_time_stop,
                "stopTimeStart": stop_time_start,
                "stopTimeStop": stop_time_stop,
                "filters": filters,
                "first": first,
                "after": after,
                "orderBy": order_by,
                "orderMode": order_mode,
            },
        )
        return self.opencti.process_multiple(
            result["data"]["stixCyberObservableRelationships"], with_pagination
        )

    """
        Read a stix_observable_relationship object

        :param id: the id of the stix_observable_relationship
        :param stix_id: the STIX id of the stix_observable_relationship
        :param fromId: the id of the source entity of the relation
        :param toId: the id of the target entity of the relation
        :param relationship_type: the relation type
        :param startTimeStart: the first_seen date start filter
        :param startTimeStop: the first_seen date stop filter
        :param stopTimeStart: the last_seen date start filter
        :param stopTimeStop: the last_seen date stop filter
        :return stix_observable_relationship object
    """

    def read(self, **kwargs):
        id = kwargs.get("id", None)
        element_id = kwargs.get("elementId", None)
        from_id = kwargs.get("fromId", None)
        to_id = kwargs.get("toId", None)
        relationship_type = kwargs.get("relationship_type", None)
        start_time_start = kwargs.get("startTimeStart", None)
        start_time_stop = kwargs.get("startTimeStop", None)
        stop_time_start = kwargs.get("stopTimeStart", None)
        stop_time_stop = kwargs.get("stopTimeStop", None)
        custom_attributes = kwargs.get("customAttributes", None)
        if id is not None:
            self.opencti.log(
                "info", "Reading stix_observable_relationship {" + id + "}."
            )
            query = (
                """
                query StixCyberObservableRelationship($id: String!) {
                    stixCyberObservableRelationship(id: $id) {
                        """
                + (
                    custom_attributes
                    if custom_attributes is not None
                    else self.properties
                )
                + """
                    }
                }
             """
            )
            result = self.opencti.query(query, {"id": id})
            return self.opencti.process_multiple_fields(
                result["data"]["stixCyberObservableRelationship"]
            )
        else:
            result = self.list(
                elementId=element_id,
                fromId=from_id,
                toId=to_id,
                relationship_type=relationship_type,
                startTimeStart=start_time_start,
                startTimeStop=start_time_stop,
                stopTimeStart=stop_time_start,
                stopTimeStop=stop_time_stop,
            )
            if len(result) > 0:
                return result[0]
            else:
                return None

    """
        Create a stix_observable_relationship object

        :param from_id: id of the source entity
        :return stix_observable_relationship object
    """

    def create(self, **kwargs):
        from_id = kwargs.get("fromId", None)
        to_id = kwargs.get("toId", None)
        relationship_type = kwargs.get("relationship_type", None)
        start_time = kwargs.get("start_time", None)
        stop_time = kwargs.get("stop_time", None)
        stix_id = kwargs.get("stix_id", None)
        created = kwargs.get("created", None)
        modified = kwargs.get("modified", None)
        created_by = kwargs.get("createdBy", None)
        object_marking = kwargs.get("objectMarking", None)
        x_opencti_stix_ids = kwargs.get("x_opencti_stix_ids", None)
        update = kwargs.get("update", False)
        self.opencti.log(
            "info",
            "Creating stix_observable_relationship {" + from_id + ", " + to_id + "}.",
        )
        query = """
                mutation StixCyberObservableRelationshipAdd($input: StixCyberObservableRelationshipAddInput!) {
                    stixCyberObservableRelationshipAdd(input: $input) {
                        id
                        standard_id
                        entity_type
                        parent_types
                    }
                }
                """
        result = self.opencti.query(
            query,
            {
                "input": {
                    "fromId": from_id,
                    "toId": to_id,
                    "relationship_type": relationship_type,
                    "start_time": start_time,
                    "stop_time": stop_time,
                    "stix_id": stix_id,
                    "created": created,
                    "modified": modified,
                    "createdBy": created_by,
                    "objectMarking": object_marking,
                    "x_opencti_stix_ids": x_opencti_stix_ids,
                    "update": update,
                }
            },
        )
        return self.opencti.process_multiple_fields(
            result["data"]["stixCyberObservableRelationshipAdd"]
        )

    """
        Update a stix_observable_relationship object field

        :param id: the stix_observable_relationship id
        :param input: the input of the field
        :return The updated stix_observable_relationship object
    """

    def update_field(self, **kwargs):
        id = kwargs.get("id", None)
        input = kwargs.get("input", None)
        if id is not None and input is not None:
            self.opencti.log(
                "info",
                "Updating stix_observable_relationship {" + id + "}.",
            )
            query = (
                """
                mutation StixCyberObservableRelationshipEdit($id: ID!, $input: [EditInput]!) {
                    stixCyberObservableRelationshipEdit(id: $id) {
                        fieldPatch(input: $input) {
                            """
                + self.properties
                + """
                        }
                    }
                }
            """
            )
            result = self.opencti.query(query, {"id": id, "input": input})
            return self.opencti.process_multiple_fields(
                result["data"]["stixCyberObservableRelationshipEdit"]["fieldPatch"]
            )
        else:
            self.opencti.log("error", "Missing parameters: id and key and value")
            return None
