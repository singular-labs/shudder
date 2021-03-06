# Copyright 2014 Scopely, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Start polling of SQS and metadata."""
import time
import shudder.queue as queue
import shudder.metadata as metadata
from shudder.config import CONFIG, LOG_FILE
from datadog import DogStatsd
import time
import requests
import signal
import subprocess
import sys
import logging
from requests.exceptions import ConnectionError

logging.basicConfig(filename=LOG_FILE, format='%(asctime)s %(levelname)s:%(message)s', level=logging.INFO)

EVENT_COUNT_METRIC = "attribution_gate.graceful_shutdown.event.count"
EVENT_TIME_METRIC = "attribution_gate.graceful_shutdown.event.time"


def receive_signal(signum, stack):
    if signum in [1, 2, 3, 15]:
        logging.info('Caught signal %s, exiting.' % (str(signum)))
        sys.exit()
    else:
        logging.info('Caught signal %s, ignoring.' % (str(signum)))


def summary_process(start_time):
    statsd.gauge(EVENT_COUNT_METRIC, 1, ["event_name:finish_process"])
    total_time = time.time() - start_time
    statsd.gauge(EVENT_TIME_METRIC, total_time, ["event_name:duration_process"])


def run_commands():
    start_time = time.time()
    for command in CONFIG["commands"]:
        try:
            statsd.gauge(EVENT_COUNT_METRIC, 1, ["event_name:heartbeat"])

            if command[0] == "RUN SUMMARY PROCESS":
                summary_process(start_time)
            else:
                logging.info('Running command: %s' % command)
                process = subprocess.Popen(command)
                while process.poll() is None:
                    time.sleep(5)
                    """Send a heart beat to aws"""
                    logging.info("sending a heart beat to aws")
                    queue.record_lifecycle_action_heartbeat(message)
        except Exception:
            logging.exception("failed running command %s" % command)


if __name__ == '__main__':
    uncatchable = ['SIG_DFL', 'SIGSTOP', 'SIGKILL']
    for i in [x for x in dir(signal) if x.startswith("SIG")]:
        if not i in uncatchable:
            signum = getattr(signal, i)
            signal.signal(signum, receive_signal)

    sqs_connection, sqs_queue = queue.create_queue()
    sns_connection, subscription_arn = queue.subscribe_sns(sqs_queue)
    statsd = DogStatsd()
    while True:
        try:
            statsd.gauge(EVENT_COUNT_METRIC, 1, ["event_name:heartbeat"])
            message = queue.poll_queue(sqs_connection, sqs_queue)

            if message or metadata.poll_instance_metadata():
                statsd.gauge(EVENT_COUNT_METRIC, 1, ["event_name:start_process"])
                logging.info("starting graceful shutdown..")
                if 'endpoint' in CONFIG:
                    requests.get(CONFIG["endpoint"])
                if 'endpoints' in CONFIG:
                    for endpoint in CONFIG["endpoints"]:
                        requests.get(endpoint)
                if 'commands' in CONFIG:
                    run_commands()

                queue.clean_up_sns(sns_connection, subscription_arn, sqs_queue)
                """Send a complete lifecycle action"""
                queue.complete_lifecycle_action(message)
                break
            time.sleep(5)
        except ConnectionError:
            logging.exception('Connection issue')
        except:
            logging.exception('Something went wrong')
