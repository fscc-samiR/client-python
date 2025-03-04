import base64
import datetime
import json
import logging
import os
import signal
import ssl
import sys
import threading
import time
import traceback
import uuid
from typing import Callable, Dict, List, Optional, Union

import pika
from pika.exceptions import NackError, UnroutableError
from sseclient import SSEClient

from pycti.api.opencti_api_client import OpenCTIApiClient
from pycti.connector.opencti_connector import OpenCTIConnector
from pycti.utils.opencti_stix2_splitter import OpenCTIStix2Splitter

TRUTHY: List[str] = ["yes", "true", "True"]
FALSY: List[str] = ["no", "false", "False"]


def killProgramHook(etype, value, tb):
    traceback.print_exception(etype, value, tb)
    os.kill(os.getpid(), signal.SIGKILL)


sys.excepthook = killProgramHook


def get_config_variable(
    env_var: str,
    yaml_path: List,
    config: Dict = {},
    isNumber: Optional[bool] = False,
    default=None,
) -> Union[bool, int, None, str]:
    """[summary]

    :param env_var: environnement variable name
    :param yaml_path: path to yaml config
    :param config: client config dict, defaults to {}
    :param isNumber: specify if the variable is a number, defaults to False
    """

    if os.getenv(env_var) is not None:
        result = os.getenv(env_var)
    elif yaml_path is not None:
        if yaml_path[0] in config and yaml_path[1] in config[yaml_path[0]]:
            result = config[yaml_path[0]][yaml_path[1]]
        else:
            return default
    else:
        return default

    if result in TRUTHY:
        return True
    if result in FALSY:
        return False
    if isNumber:
        return int(result)

    return result


def create_ssl_context() -> ssl.SSLContext:
    """Set strong SSL defaults: require TLSv1.2+

    `ssl` uses bitwise operations to specify context `<enum 'Options'>`
    """

    ssl_context_options: List[int] = [
        ssl.OP_NO_COMPRESSION,
        ssl.OP_NO_TICKET,  # pylint: disable=no-member
        ssl.OP_NO_RENEGOTIATION,  # pylint: disable=no-member
        ssl.OP_SINGLE_DH_USE,
        ssl.OP_SINGLE_ECDH_USE,
    ]
    ssl_context = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
    ssl_context.options &= ~ssl.OP_ENABLE_MIDDLEBOX_COMPAT  # pylint: disable=no-member
    ssl_context.verify_mode = ssl.CERT_REQUIRED
    ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2

    for option in ssl_context_options:
        ssl_context.options |= option

    return ssl_context


class ListenQueue(threading.Thread):
    """Main class for the ListenQueue used in OpenCTIConnectorHelper

    :param helper: instance of a `OpenCTIConnectorHelper` class
    :type helper: OpenCTIConnectorHelper
    :param config: dict containing client config
    :type config: Dict
    :param callback: callback function to process queue
    :type callback: callable
    """

    def __init__(self, helper, config: Dict, callback) -> None:
        threading.Thread.__init__(self)
        self.pika_credentials = None
        self.pika_parameters = None
        self.pika_connection = None
        self.channel = None
        self.helper = helper
        self.callback = callback
        self.host = config["connection"]["host"]
        self.use_ssl = config["connection"]["use_ssl"]
        self.port = config["connection"]["port"]
        self.user = config["connection"]["user"]
        self.password = config["connection"]["pass"]
        self.queue_name = config["listen"]
        self.exit_event = threading.Event()
        self.thread = None

    # noinspection PyUnusedLocal
    def _process_message(self, channel, method, properties, body) -> None:
        """process a message from the rabbit queue

        :param channel: channel instance
        :type channel: callable
        :param method: message methods
        :type method: callable
        :param properties: unused
        :type properties: str
        :param body: message body (data)
        :type body: str or bytes or bytearray
        """

        json_data = json.loads(body)
        channel.basic_ack(delivery_tag=method.delivery_tag)
        self.thread = threading.Thread(target=self._data_handler, args=[json_data])
        self.thread.start()
        five_minutes = 60 * 5
        time_wait = 0
        while self.thread.is_alive():  # Loop while the thread is processing
            if (
                self.helper.work_id is not None and time_wait > five_minutes
            ):  # Ping every 5 minutes
                self.helper.api.work.ping(self.helper.work_id)
                time_wait = 0
            else:
                time_wait += 1
            time.sleep(1)

        logging.info(
            "%s",
            (
                f"Message (delivery_tag={method.delivery_tag}) processed"
                ", thread terminated"
            ),
        )

    def _data_handler(self, json_data) -> None:
        # Set the API headers
        work_id = json_data["internal"]["work_id"]
        applicant_id = json_data["internal"]["applicant_id"]
        self.helper.work_id = work_id
        if applicant_id is not None:
            self.helper.applicant_id = applicant_id
            self.helper.api.set_applicant_id_header(applicant_id)
        # Execute the callback
        try:
            self.helper.api.work.to_received(
                work_id, "Connector ready to process the operation"
            )
            message = self.callback(json_data["event"])
            self.helper.api.work.to_processed(work_id, message)
        except Exception as e:  # pylint: disable=broad-except
            logging.exception("Error in message processing, reporting error to API")
            try:
                self.helper.api.work.to_processed(work_id, str(e), True)
            except:  # pylint: disable=bare-except
                logging.error("Failing reporting the processing")

    def run(self) -> None:
        while not self.exit_event.is_set():
            try:
                # Connect the broker
                self.pika_credentials = pika.PlainCredentials(self.user, self.password)
                self.pika_parameters = pika.ConnectionParameters(
                    host=self.host,
                    port=self.port,
                    virtual_host="/",
                    credentials=self.pika_credentials,
                    ssl_options=pika.SSLOptions(create_ssl_context(), self.host)
                    if self.use_ssl
                    else None,
                )
                self.pika_connection = pika.BlockingConnection(self.pika_parameters)
                self.channel = self.pika_connection.channel()
                assert self.channel is not None
                self.channel.basic_consume(
                    queue=self.queue_name, on_message_callback=self._process_message
                )
                self.channel.start_consuming()
            except (KeyboardInterrupt, SystemExit):
                self.helper.log_info("Connector stop")
                sys.exit(0)
            except Exception as e:  # pylint: disable=broad-except
                self.helper.log_error(str(e))
                time.sleep(10)

    def stop(self):
        self.exit_event.set()
        if self.thread:
            self.thread.join()


class PingAlive(threading.Thread):
    def __init__(self, connector_id, api, get_state, set_state) -> None:
        threading.Thread.__init__(self)
        self.connector_id = connector_id
        self.in_error = False
        self.api = api
        self.get_state = get_state
        self.set_state = set_state
        self.exit_event = threading.Event()

    def ping(self) -> None:
        while not self.exit_event.is_set():
            try:
                initial_state = self.get_state()
                result = self.api.connector.ping(self.connector_id, initial_state)
                remote_state = (
                    json.loads(result["connector_state"])
                    if result["connector_state"] is not None
                    and len(result["connector_state"]) > 0
                    else None
                )
                if initial_state != remote_state:
                    self.set_state(result["connector_state"])
                    logging.info(
                        "%s",
                        (
                            "Connector state has been remotely reset to: "
                            f'"{self.get_state()}"'
                        ),
                    )
                if self.in_error:
                    self.in_error = False
                    logging.error("API Ping back to normal")
            except Exception:  # pylint: disable=broad-except
                self.in_error = True
                logging.error("Error pinging the API")
            self.exit_event.wait(40)

    def run(self) -> None:
        logging.info("Starting ping alive thread")
        self.ping()

    def stop(self) -> None:
        logging.info("Preparing for clean shutdown")
        self.exit_event.set()


class ListenStream(threading.Thread):
    def __init__(
        self,
        helper,
        callback,
        url,
        token,
        verify_ssl,
        start_timestamp,
        live_stream_id,
        listen_delete,
        no_dependencies,
    ) -> None:
        threading.Thread.__init__(self)
        self.helper = helper
        self.callback = callback
        self.url = url
        self.token = token
        self.verify_ssl = verify_ssl
        self.start_timestamp = start_timestamp
        self.live_stream_id = live_stream_id
        self.listen_delete = listen_delete if listen_delete is not None else True
        self.no_dependencies = no_dependencies if no_dependencies is not None else False
        self.exit = False

    def run(self) -> None:  # pylint: disable=too-many-branches
        try:
            current_state = self.helper.get_state()
            if current_state is None:
                current_state = {
                    "connectorLastEventId": f"{self.start_timestamp}-0"
                    if self.start_timestamp is not None
                    and len(self.start_timestamp) > 0
                    else "-"
                }
                self.helper.set_state(current_state)

            # If URL and token are provided, likely consuming a remote stream
            if self.url is not None and self.token is not None:
                # If a live stream ID, appending the URL
                if self.live_stream_id is not None:
                    live_stream_uri = f"/{self.live_stream_id}"
                elif self.helper.connect_live_stream_id is not None:
                    live_stream_uri = f"/{self.helper.connect_live_stream_id}"
                else:
                    live_stream_uri = ""
                # Live stream "from" should be empty if start from the beginning
                if (
                    self.live_stream_id is not None
                    or self.helper.connect_live_stream_id is not None
                ):
                    live_stream_from = (
                        f"?from={current_state['connectorLastEventId']}"
                        if current_state["connectorLastEventId"] != "-"
                        else ""
                    )
                # Global stream "from" should be 0 if starting from the beginning
                else:
                    live_stream_from = "?from=" + (
                        current_state["connectorLastEventId"]
                        if current_state["connectorLastEventId"] != "-"
                        else "0"
                    )
                live_stream_url = (
                    f"{self.url}/stream{live_stream_uri}{live_stream_from}"
                )
                opencti_ssl_verify = (
                    self.verify_ssl if self.verify_ssl is not None else True
                )
                logging.info(
                    "%s",
                    (
                        "Starting listening stream events (URL: "
                        f"{live_stream_url}, SSL verify: {opencti_ssl_verify}, Listen Delete: {self.listen_delete})"
                    ),
                )
                messages = SSEClient(
                    live_stream_url,
                    headers={
                        "authorization": "Bearer " + self.token,
                        "listen-delete": "false"
                        if self.listen_delete is False
                        else "true",
                        "no-dependencies": "true"
                        if self.no_dependencies is True
                        else "false",
                    },
                    verify=opencti_ssl_verify,
                )
            else:
                live_stream_uri = (
                    f"/{self.helper.connect_live_stream_id}"
                    if self.helper.connect_live_stream_id is not None
                    else ""
                )
                if self.helper.connect_live_stream_id is not None:
                    live_stream_from = (
                        f"?from={current_state['connectorLastEventId']}"
                        if current_state["connectorLastEventId"] != "-"
                        else ""
                    )
                # Global stream "from" should be 0 if starting from the beginning
                else:
                    live_stream_from = "?from=" + (
                        current_state["connectorLastEventId"]
                        if current_state["connectorLastEventId"] != "-"
                        else "0"
                    )
                live_stream_url = f"{self.helper.opencti_url}/stream{live_stream_uri}{live_stream_from}"
                logging.info(
                    "%s",
                    (
                        f"Starting listening stream events (URL: {live_stream_url}"
                        f", SSL verify: {self.helper.opencti_ssl_verify}, Listen Delete: {self.helper.connect_live_stream_listen_delete}, No Dependencies: {self.helper.connect_live_stream_no_dependencies})"
                    ),
                )
                messages = SSEClient(
                    live_stream_url,
                    headers={
                        "authorization": "Bearer " + self.helper.opencti_token,
                        "listen-delete": "false"
                        if self.helper.connect_live_stream_listen_delete is False
                        else "true",
                        "no-dependencies": "true"
                        if self.helper.connect_live_stream_no_dependencies is True
                        else "false",
                    },
                    verify=self.helper.opencti_ssl_verify,
                )
            # Iter on stream messages
            for msg in messages:
                if self.exit:
                    break
                if msg.event == "heartbeat" or msg.event == "connected":
                    continue
                if msg.event == "sync":
                    if msg.id is not None:
                        state = self.helper.get_state()
                        state["connectorLastEventId"] = str(msg.id)
                        self.helper.set_state(state)
                else:
                    self.callback(msg)
                    if msg.id is not None:
                        state = self.helper.get_state()
                        state["connectorLastEventId"] = str(msg.id)
                        self.helper.set_state(state)
        except:
            sys.excepthook(*sys.exc_info())

    def stop(self):
        self.exit = True


class OpenCTIConnectorHelper:  # pylint: disable=too-many-public-methods
    """Python API for OpenCTI connector

    :param config: dict standard config
    :type config: Dict
    """

    def __init__(self, config: Dict) -> None:
        # Load API config
        self.opencti_url = get_config_variable(
            "OPENCTI_URL", ["opencti", "url"], config
        )
        self.opencti_token = get_config_variable(
            "OPENCTI_TOKEN", ["opencti", "token"], config
        )
        self.opencti_ssl_verify = get_config_variable(
            "OPENCTI_SSL_VERIFY", ["opencti", "ssl_verify"], config, False, True
        )
        self.opencti_json_logging = get_config_variable(
            "OPENCTI_JSON_LOGGING", ["opencti", "json_logging"], config
        )
        # Load connector config
        self.connect_id = get_config_variable(
            "CONNECTOR_ID", ["connector", "id"], config
        )
        self.connect_type = get_config_variable(
            "CONNECTOR_TYPE", ["connector", "type"], config
        )
        self.connect_live_stream_id = get_config_variable(
            "CONNECTOR_LIVE_STREAM_ID",
            ["connector", "live_stream_id"],
            config,
            False,
            None,
        )
        self.connect_live_stream_listen_delete = get_config_variable(
            "CONNECTOR_LIVE_STREAM_LISTEN_DELETE",
            ["connector", "live_stream_listen_delete"],
            config,
            False,
            True,
        )
        self.connect_live_stream_no_dependencies = get_config_variable(
            "CONNECTOR_LIVE_STREAM_NO_DEPENDENCIES",
            ["connector", "live_stream_no_dependencies"],
            config,
            False,
            False,
        )
        self.connect_name = get_config_variable(
            "CONNECTOR_NAME", ["connector", "name"], config
        )
        self.connect_confidence_level = get_config_variable(
            "CONNECTOR_CONFIDENCE_LEVEL",
            ["connector", "confidence_level"],
            config,
            True,
        )
        self.connect_scope = get_config_variable(
            "CONNECTOR_SCOPE", ["connector", "scope"], config
        )
        self.connect_auto = get_config_variable(
            "CONNECTOR_AUTO", ["connector", "auto"], config, False, False
        )
        self.connect_only_contextual = get_config_variable(
            "CONNECTOR_ONLY_CONTEXTUAL",
            ["connector", "only_contextual"],
            config,
            False,
            False,
        )
        self.log_level = get_config_variable(
            "CONNECTOR_LOG_LEVEL", ["connector", "log_level"], config
        )
        self.connect_run_and_terminate = get_config_variable(
            "CONNECTOR_RUN_AND_TERMINATE",
            ["connector", "run_and_terminate"],
            config,
            False,
            False,
        )
        self.connect_validate_before_import = get_config_variable(
            "CONNECTOR_VALIDATE_BEFORE_IMPORT",
            ["connector", "validate_before_import"],
            config,
            False,
            False,
        )

        # Configure logger
        numeric_level = getattr(
            logging, self.log_level.upper() if self.log_level else "INFO", None
        )
        if not isinstance(numeric_level, int):
            raise ValueError(f"Invalid log level: {self.log_level}")
        logging.basicConfig(level=numeric_level)

        # Initialize configuration
        self.api = OpenCTIApiClient(
            self.opencti_url,
            self.opencti_token,
            self.log_level,
            json_logging=self.opencti_json_logging,
        )
        # Register the connector in OpenCTI
        self.connector = OpenCTIConnector(
            self.connect_id,
            self.connect_name,
            self.connect_type,
            self.connect_scope,
            self.connect_auto,
            self.connect_only_contextual,
        )
        connector_configuration = self.api.connector.register(self.connector)
        logging.info("%s", f"Connector registered with ID: {self.connect_id}")
        self.connector_id = connector_configuration["id"]
        self.work_id = None
        self.applicant_id = connector_configuration["connector_user"]["id"]
        self.connector_state = connector_configuration["connector_state"]
        self.config = connector_configuration["config"]

        # Start ping thread
        if not self.connect_run_and_terminate:
            self.ping = PingAlive(
                self.connector.id, self.api, self.get_state, self.set_state
            )
            self.ping.start()

        # self.listen_stream = None
        self.listen_queue = None

    def stop(self) -> None:
        if self.listen_queue:
            self.listen_queue.stop()
        # if self.listen_stream:
        #     self.listen_stream.stop()
        self.ping.stop()
        self.api.connector.unregister(self.connector_id)

    def get_name(self) -> Optional[Union[bool, int, str]]:
        return self.connect_name

    def get_only_contextual(self) -> Optional[Union[bool, int, str]]:
        return self.connect_only_contextual

    def get_run_and_terminate(self) -> Optional[Union[bool, int, str]]:
        return self.connect_run_and_terminate

    def get_validate_before_import(self) -> Optional[Union[bool, int, str]]:
        return self.connect_validate_before_import

    def set_state(self, state) -> None:
        """sets the connector state

        :param state: state object
        :type state: Dict
        """

        self.connector_state = json.dumps(state)

    def get_state(self) -> Optional[Dict]:
        """get the connector state

        :return: returns the current state of the connector if there is any
        :rtype:
        """

        try:
            if self.connector_state:
                state = json.loads(self.connector_state)
                if isinstance(state, Dict) and state:
                    return state
        except:  # pylint: disable=bare-except  # noqa: E722
            pass
        return None

    def force_ping(self):
        try:
            initial_state = self.get_state()
            result = self.api.connector.ping(self.connector_id, initial_state)
            remote_state = (
                json.loads(result["connector_state"])
                if result["connector_state"] is not None
                and len(result["connector_state"]) > 0
                else None
            )
            if initial_state != remote_state:
                self.api.connector.ping(self.connector_id, initial_state)
        except Exception:  # pylint: disable=broad-except
            logging.error("Error pinging the API")

    def listen(self, message_callback: Callable[[Dict], str]) -> None:
        """listen for messages and register callback function

        :param message_callback: callback function to process messages
        :type message_callback: Callable[[Dict], str]
        """

        self.listen_queue = ListenQueue(self, self.config, message_callback)
        self.listen_queue.start()

    def listen_stream(
        self,
        message_callback,
        url=None,
        token=None,
        verify_ssl=None,
        start_timestamp=None,
        live_stream_id=None,
        listen_delete=True,
        no_dependencies=False,
    ) -> ListenStream:
        """listen for messages and register callback function

        :param message_callback: callback function to process messages
        """

        self.listen_stream = ListenStream(
            self,
            message_callback,
            url,
            token,
            verify_ssl,
            start_timestamp,
            live_stream_id,
            listen_delete,
            no_dependencies,
        )
        self.listen_stream.start()
        return self.listen_stream

    def get_opencti_url(self) -> Optional[Union[bool, int, str]]:
        return self.opencti_url

    def get_opencti_token(self) -> Optional[Union[bool, int, str]]:
        return self.opencti_token

    def get_connector(self) -> OpenCTIConnector:
        return self.connector

    def log_error(self, msg: str) -> None:
        logging.error(msg)

    def log_info(self, msg: str) -> None:
        logging.info(msg)

    def log_debug(self, msg: str) -> None:
        logging.debug(msg)

    def log_warning(self, msg: str) -> None:
        logging.warning(msg)

    def date_now(self) -> str:
        """get the current date (UTC)
        :return: current datetime for utc
        :rtype: str
        """
        return (
            datetime.datetime.utcnow()
            .replace(microsecond=0, tzinfo=datetime.timezone.utc)
            .isoformat()
        )

    # Push Stix2 helper
    def send_stix2_bundle(self, bundle, **kwargs) -> list:
        """send a stix2 bundle to the API

        :param work_id: a valid work id
        :param bundle: valid stix2 bundle
        :type bundle:
        :param entities_types: list of entities, defaults to None
        :type entities_types: list, optional
        :param update: whether to updated data in the database, defaults to False
        :type update: bool, optional
        :raises ValueError: if the bundle is empty
        :return: list of bundles
        :rtype: list
        """
        work_id = kwargs.get("work_id", self.work_id)
        entities_types = kwargs.get("entities_types", None)
        update = kwargs.get("update", False)
        event_version = kwargs.get("event_version", None)
        bypass_split = kwargs.get("bypass_split", False)
        bypass_validation = kwargs.get("bypass_validation", False)
        entity_id = kwargs.get("entity_id", None)
        file_name = kwargs.get("file_name", None)

        if not file_name and work_id:
            file_name = f"{work_id}.json"

        if self.connect_validate_before_import and not bypass_validation and file_name:
            self.api.upload_pending_file(
                file_name=file_name,
                data=bundle,
                mime_type="application/json",
                entity_id=entity_id,
            )
            return []

        if entities_types is None:
            entities_types = []

        if bypass_split:
            bundles = [bundle]
        else:
            stix2_splitter = OpenCTIStix2Splitter()
            bundles = stix2_splitter.split_bundle(bundle, True, event_version)

        if len(bundles) == 0:
            raise ValueError("Nothing to import")

        if work_id:
            self.api.work.add_expectations(work_id, len(bundles))

        pika_credentials = pika.PlainCredentials(
            self.config["connection"]["user"], self.config["connection"]["pass"]
        )
        pika_parameters = pika.ConnectionParameters(
            host=self.config["connection"]["host"],
            port=self.config["connection"]["port"],
            virtual_host="/",
            credentials=pika_credentials,
            ssl_options=pika.SSLOptions(
                create_ssl_context(), self.config["connection"]["host"]
            )
            if self.config["connection"]["use_ssl"]
            else None,
        )

        pika_connection = pika.BlockingConnection(pika_parameters)
        channel = pika_connection.channel()
        for sequence, bundle in enumerate(bundles, start=1):
            self._send_bundle(
                channel,
                bundle,
                work_id=work_id,
                entities_types=entities_types,
                sequence=sequence,
                update=update,
            )
        channel.close()
        return bundles

    def _send_bundle(self, channel, bundle, **kwargs) -> None:
        """send a STIX2 bundle to RabbitMQ to be consumed by workers

        :param channel: RabbitMQ channel
        :type channel: callable
        :param bundle: valid stix2 bundle
        :type bundle:
        :param entities_types: list of entity types, defaults to None
        :type entities_types: list, optional
        :param update: whether to update data in the database, defaults to False
        :type update: bool, optional
        """
        work_id = kwargs.get("work_id", None)
        sequence = kwargs.get("sequence", 0)
        update = kwargs.get("update", False)
        entities_types = kwargs.get("entities_types", None)

        if entities_types is None:
            entities_types = []

        # Validate the STIX 2 bundle
        # validation = validate_string(bundle)
        # if not validation.is_valid:
        # raise ValueError('The bundle is not a valid STIX2 JSON')

        # Prepare the message
        # if self.current_work_id is None:
        #    raise ValueError('The job id must be specified')
        message = {
            "applicant_id": self.applicant_id,
            "action_sequence": sequence,
            "entities_types": entities_types,
            "content": base64.b64encode(bundle.encode("utf-8")).decode("utf-8"),
            "update": update,
        }
        if work_id is not None:
            message["work_id"] = work_id

        # Send the message
        try:
            routing_key = "push_routing_" + self.connector_id
            channel.basic_publish(
                exchange=self.config["push_exchange"],
                routing_key=routing_key,
                body=json.dumps(message),
                properties=pika.BasicProperties(
                    delivery_mode=2,  # make message persistent
                ),
            )
            logging.info("Bundle has been sent")
        except (UnroutableError, NackError) as e:
            logging.error("Unable to send bundle, retry...%s", e)
            self._send_bundle(channel, bundle, **kwargs)

    def split_stix2_bundle(self, bundle) -> list:
        """splits a valid stix2 bundle into a list of bundles

        :param bundle: valid stix2 bundle
        :type bundle:
        :raises Exception: if data is not valid JSON
        :return: returns a list of bundles
        :rtype: list
        """

        self.cache_index = {}
        self.cache_added = []
        try:
            bundle_data = json.loads(bundle)
        except Exception as e:
            raise Exception("File data is not a valid JSON") from e

        # validation = validate_parsed_json(bundle_data)
        # if not validation.is_valid:
        #     raise ValueError('The bundle is not a valid STIX2 JSON:' + bundle)

        # Index all objects by id
        for item in bundle_data["objects"]:
            self.cache_index[item["id"]] = item

        bundles = []
        # Reports must be handled because of object_refs
        for item in bundle_data["objects"]:
            if item["type"] == "report":
                items_to_send = self.stix2_deduplicate_objects(
                    self.stix2_get_report_objects(item)
                )
                for item_to_send in items_to_send:
                    self.cache_added.append(item_to_send["id"])
                bundles.append(self.stix2_create_bundle(items_to_send))

        # Relationships not added in previous reports
        for item in bundle_data["objects"]:
            if item["type"] == "relationship" and item["id"] not in self.cache_added:
                items_to_send = self.stix2_deduplicate_objects(
                    self.stix2_get_relationship_objects(item)
                )
                for item_to_send in items_to_send:
                    self.cache_added.append(item_to_send["id"])
                bundles.append(self.stix2_create_bundle(items_to_send))

        # Entities not added in previous reports and relationships
        for item in bundle_data["objects"]:
            if item["type"] != "relationship" and item["id"] not in self.cache_added:
                items_to_send = self.stix2_deduplicate_objects(
                    self.stix2_get_entity_objects(item)
                )
                for item_to_send in items_to_send:
                    self.cache_added.append(item_to_send["id"])
                bundles.append(self.stix2_create_bundle(items_to_send))

        return bundles

    def stix2_get_embedded_objects(self, item) -> Dict:
        """gets created and marking refs for a stix2 item

        :param item: valid stix2 item
        :type item:
        :return: returns a dict of created_by of object_marking_refs
        :rtype: Dict
        """
        # Marking definitions
        object_marking_refs = []
        if "object_marking_refs" in item:
            for object_marking_ref in item["object_marking_refs"]:
                if object_marking_ref in self.cache_index:
                    object_marking_refs.append(self.cache_index[object_marking_ref])
        # Created by ref
        created_by_ref = None
        if "created_by_ref" in item and item["created_by_ref"] in self.cache_index:
            created_by_ref = self.cache_index[item["created_by_ref"]]

        return {
            "object_marking_refs": object_marking_refs,
            "created_by_ref": created_by_ref,
        }

    def stix2_get_entity_objects(self, entity) -> list:
        """process a stix2 entity

        :param entity: valid stix2 entity
        :type entity:
        :return: entity objects as list
        :rtype: list
        """

        items = [entity]
        # Get embedded objects
        embedded_objects = self.stix2_get_embedded_objects(entity)
        # Add created by ref
        if embedded_objects["created_by_ref"] is not None:
            items.append(embedded_objects["created_by_ref"])
        # Add marking definitions
        if len(embedded_objects["object_marking_refs"]) > 0:
            items = items + embedded_objects["object_marking_refs"]

        return items

    def stix2_get_relationship_objects(self, relationship) -> list:
        """get a list of relations for a stix2 relationship object

        :param relationship: valid stix2 relationship
        :type relationship:
        :return: list of relations objects
        :rtype: list
        """

        items = [relationship]
        # Get source ref
        if relationship["source_ref"] in self.cache_index:
            items.append(self.cache_index[relationship["source_ref"]])

        # Get target ref
        if relationship["target_ref"] in self.cache_index:
            items.append(self.cache_index[relationship["target_ref"]])

        # Get embedded objects
        embedded_objects = self.stix2_get_embedded_objects(relationship)
        # Add created by ref
        if embedded_objects["created_by"] is not None:
            items.append(embedded_objects["created_by"])
        # Add marking definitions
        if len(embedded_objects["object_marking_refs"]) > 0:
            items = items + embedded_objects["object_marking_refs"]

        return items

    def stix2_get_report_objects(self, report) -> list:
        """get a list of items for a stix2 report object

        :param report: valid stix2 report object
        :type report:
        :return: list of items for a stix2 report object
        :rtype: list
        """

        items = [report]
        # Add all object refs
        for object_ref in report["object_refs"]:
            items.append(self.cache_index[object_ref])
        for item in items:
            if item["type"] == "relationship":
                items = items + self.stix2_get_relationship_objects(item)
            else:
                items = items + self.stix2_get_entity_objects(item)
        return items

    @staticmethod
    def stix2_deduplicate_objects(items) -> list:
        """deduplicate stix2 items

        :param items: valid stix2 items
        :type items:
        :return: de-duplicated list of items
        :rtype: list
        """

        ids = []
        final_items = []
        for item in items:
            if item["id"] not in ids:
                final_items.append(item)
                ids.append(item["id"])
        return final_items

    @staticmethod
    def stix2_create_bundle(items) -> Optional[str]:
        """create a stix2 bundle with items

        :param items: valid stix2 items
        :type items:
        :return: JSON of the stix2 bundle
        :rtype:
        """

        bundle = {
            "type": "bundle",
            "id": f"bundle--{uuid.uuid4()}",
            "spec_version": "2.0",
            "objects": items,
        }
        return json.dumps(bundle)

    @staticmethod
    def check_max_tlp(tlp: str, max_tlp: str) -> bool:
        """check the allowed TLP levels for a TLP string

        :param tlp: string for TLP level to check
        :type tlp: str
        :param max_tlp: the highest allowed TLP level
        :type max_tlp: str
        :return: TLP level in allowed TLPs
        :rtype: bool
        """

        allowed_tlps: Dict[str, List[str]] = {
            "TLP:RED": ["TLP:WHITE", "TLP:GREEN", "TLP:AMBER", "TLP:RED"],
            "TLP:AMBER": ["TLP:WHITE", "TLP:GREEN", "TLP:AMBER"],
            "TLP:GREEN": ["TLP:WHITE", "TLP:GREEN"],
            "TLP:WHITE": ["TLP:WHITE"],
        }

        return tlp in allowed_tlps[max_tlp]
