#!/usr/bin/env python3
import os
import sys
import json
import argparse
import logging

import paho.mqtt.client as mqtt
import tenacity
import pyrtstools as rts

if getattr(sys, 'frozen', False):
    DIR_PATH = os.path.dirname(sys.executable)
else:
    DIR_PATH = os.path.dirname(__file__)

class Command:
    """ The command class is intended to process speech command following a wake-up-word.
    It detects the keyword, the beginning and the end of spoken command and sends messages on a
    MQTT communication pipeline.
    Configuration is done using a .json file. 
    """ 
    def __init__(self):
        self._running = False
        self.config = dict() # Hold general parameters
        self.mqtt_config = dict() # Hold mqtt topics and messages
        self._action_map = dict() # Bind input mqtt message with functions
        
        self._load_config()
        
        # Find model and load param
        folder_content = [f for f in os.listdir(self.config['MODEL_FOLDER']) if f.endswith('.net') or f.endswith('.pb')]
        if len(folder_content) == 0:
            logging.error("Could not find model file in {}".format(self.config['MODEL_FOLDER']))
        model_path = os.path.join(self.config['MODEL_FOLDER'],folder_content[0])
        param_path = os.path.splitext(model_path)[0] + ".param"

        with open(param_path) as f:
            model_param = json.load(f)

        # Building the pipeline
        audio_params = rts.listenner.AudioParams(**model_param['features'])
        features_params = rts.features.MFCCParams(**model_param['features'])

        listenner = rts.listenner.Listenner(audio_params)
        self._vad = rts.vad.VADer()
        btn = rts.transform.ByteToNum(normalize=True)
        mfcc = rts.features.SonopyMFCC(features_params)
        self._kws = rts.kws.KWS(model_path, model_param['input_shape'], threshold=float(self.config['KWS_TH']))
        elements = [listenner, self._vad, btn, mfcc, self._kws]
        self.pipeline = rts.Pipeline(elements)

        # Linking the handlers
        for element in elements:
            element.on_error = self._on_error
        self._kws.on_detection = self._on_hotword

        # Setup and bind MQTT client callbacks
        self._client = mqtt.Client()
        self._client.on_connect = self._on_broker_connect
        self._client.on_disconnect = self._on_broker_disconnect
        self._client.on_message = self._on_message
        self._connect(self.config['MQTT_LOCAL_HOST'], int(self.config['MQTT_LOCAL_PORT']))
        
    def _load_config(self):
        #Load env_default value
        with open(os.path.join(DIR_PATH, '.env_default')) as f:
            lines = f.readlines()
            for line in lines:
                key, value = line.strip().split('=')
                self.config[key] = os.path.expandvars(value)
        
        #override with .env values
        env_path = os.path.join(DIR_PATH, '.env')
        if os.path.isfile(env_path):
            with open(env_path) as f:
                lines = f.readlines()
                for line in lines:
                    key, value = line.strip().split('=')
                    if key in self.config.keys():
                        self.config[key] = os.path.expandvars(value)

        #override with ENV value
        for key in [k for k in os.environ.keys() if k in self.config.keys()]:
            value = os.environ[key]
            logging.debug("Overriding value for {} with environement value {}".format(key, value))
            self.config[key] = value

        logging.debug(self.config)
        
        #read mqtt msg config
        with open(os.path.join(DIR_PATH, 'mqtt_msg.json')) as f:
            self.mqtt_config = json.load(f)

            # Map input topics with functions
            self._action_map[self.mqtt_config['input']['suspend_topic']] = self.suspend
            self._action_map[self.mqtt_config['input']['resume_topic']] = self.resume
            self._action_map[self.mqtt_config['input']['cancel_topic']] = self.cancel_utterance
            self._action_map[self.mqtt_config['input']['start_utterance']] = self.detect_utterance
    
    def _on_hotword(self, index: int, value: float):
        logging.debug("Hotword spotted {}:{}".format(index, value))
        logging.debug("Utterance start")
        self._client.publish(self.mqtt_config['output']['utterance_start']['topic'],
                             json.dumps(self.mqtt_config['output']['utterance_start']['message']))
        self.detect_utterance()

    def detect_utterance(self):
        self._kws.stop()
        logging.debug("Start utterance detection")
        self._vad.detect_utterance(callback=self._on_utterance_end)
    
    def cancel_utterance(self):
        logging.debug("Cancel utterance detection")
        self._vad.cancel_utterance()
    
    def _on_utterance_end(self, status: rts.vad.vad.Utt_Status, data: bytes):
        logging.debug("Utterance end with status {} ({}B)".format(status, len(data) if data is not None else str(0)))
        messages = [self.mqtt_config['output'][reason]['message'] for reason in ["utterance_th", "utterance_timeout", "utterance_canceled"]]
        msg = dict(zip([1,-1,0], messages))[status.value]
        if status == rts.vad.vad.Utt_Status.THREACHED:
            with open(self.config['TMP_FILE'], 'wb') as f:
                f.write(data)
        self._client.publish(self.mqtt_config['output']['utterance_stop']['topic'], json.dumps(msg))
        self._kws.resume()
    
    @tenacity.retry(wait=tenacity.wait_random(min=1, max=10),
                retry=tenacity.retry_if_result(lambda s: s is False))
    def _connect(self, host: str, port: int) -> bool:
        try:
            self._client.connect(host, port)
        except:
            logging.error("Failed to connect to broker, retrying ...")
            return False

    def _on_broker_connect(self, client, userdata, flags, rc):
        logging.info("Successfuly connected to broker at {}:{}".format(self.config['MQTT_LOCAL_HOST'], self.config['MQTT_LOCAL_PORT']))
        for key in self.mqtt_config['input'].keys():
            self._client.subscribe(self.mqtt_config['input'][key])
            logging.debug("Subscribed to {}".format(self.mqtt_config['input'][key]))
    
    def _on_broker_disconnect(self):
        logging.warning("MQTT Client has been disconnected")
        if self._running:
            self._connect(self.config['MQTT_LOCAL_HOST'], int(self.config['MQTT_LOCAL_PORT']))

    def _on_message(self, client, userdata, message):
        msg = str(message.payload.decode("utf-8"))
        topic = message.topic
        logging.debug("Incoming message ({}): {}".format(topic, msg))
        self._process_input(topic)

    def _on_error(self, err):
        logging.error("Catched Error: {}".format(err))

    def _process_input(self, topic):
        if topic in self._action_map.keys():
            self._action_map[topic]()

    def suspend(self):
        self.pipeline.stop()
        logging.debug("Process is suspended")

    def resume(self):
        self.pipeline.resume()
        logging.debug("Process is resumed")

    def start(self):
        try:
            self.pipeline.start()
            self._client.publish(self.mqtt_config['output']['ready']['topic'],
                             json.dumps(self.mqtt_config['output']['ready']['message']))
            self._client.loop_forever()
        except KeyboardInterrupt:
            logging.info("Process interrupted by user")
        finally:
            self.pipeline.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Voice Command Module')
    parser.add_argument('--debug', action="store_true", help='Prompts debug')
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format="%(levelname)8s %(asctime)s [Command] %(message)s")
    
    command = Command()
    command.start()