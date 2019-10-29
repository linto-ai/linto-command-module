#!/usr/bin/env python3
__version__ = "1.0.2"

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
        logging.info("Running command module version {}".format(__version__))
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
        audio_params = rts.listenner.AudioParams(**model_param['audio'])
        features_params = rts.features.MFCCParams(**model_param['audio'], **model_param['features'])

        listenner = rts.listenner.Listenner(audio_params)
        self._vad = rts.vad.VADer(tail=int(self.config['TAIL']), head=int(self.config['HEAD']))
        btn = rts.transform.ByteToNum(normalize=True)
        
        if 'emphasis' in model_param['audio'] and model_param['audio']['emphasis'] is not None:
            emp = rts.transform.PreEmphasis(emphasis_factor = model_param['audio']['emphasis'])
        else: 
            emp = None
        mfcc = rts.features.SonopyMFCC(features_params)
        self._kws = rts.kws.KWS(model_path, model_param['input_shape'], threshold=float(self.config['KWS_TH']), n_act_recquire=int(self.config['KWS_NACT']))
        elements = [listenner, self._vad, btn]
        if emp is not None:
            elements.append(emp)
        elements.extend([mfcc, self._kws])
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
            self._action_map[self.mqtt_config['input']['suspend']['topic']] = dict()
            self._action_map[self.mqtt_config['input']['suspend_kws']['topic']] = dict()
            self._action_map[self.mqtt_config['input']['resume']['topic']] = dict()
            self._action_map[self.mqtt_config['input']['resume_kws']['topic']] = dict()
            self._action_map[self.mqtt_config['input']['cancel']['topic']] = dict()
            self._action_map[self.mqtt_config['input']['start_utterance']['topic']] = dict()
            self._action_map[self.mqtt_config['input']['dummy_detect']['topic']] = dict()
            
            self._action_map[self.mqtt_config['input']['suspend']['topic']][self.mqtt_config['input']['suspend']['value']] = self.suspend
            self._action_map[self.mqtt_config['input']['suspend_kws']['topic']][self.mqtt_config['input']['suspend_kws']['value']] = self.suspend_kws
            self._action_map[self.mqtt_config['input']['resume']['topic']][self.mqtt_config['input']['resume']['value']] = self.resume
            self._action_map[self.mqtt_config['input']['resume_kws']['topic']][self.mqtt_config['input']['resume_kws']['value']] = self.resume_kws
            self._action_map[self.mqtt_config['input']['cancel']['topic']][self.mqtt_config['input']['cancel']['value']] = self.cancel_utterance
            self._action_map[self.mqtt_config['input']['start_utterance']['topic']][self.mqtt_config['input']['start_utterance']['value']] = self.detect_utterance
            self._action_map[self.mqtt_config['input']['dummy_detect']['topic']][self.mqtt_config['input']['dummy_detect']['value']] = self.dummy_detect

    def _on_hotword(self, index: int, value: float):
        logging.debug("Hotword spotted {}:{}".format(index, value))
        logging.debug("Utterance start")
        self.detect_utterance()

    def dummy_detect(self):
        self._on_hotword(0, 1.0)

    def detect_utterance(self):
        self._kws.stop()
        self._client.publish(self.mqtt_config['output']['utterance_start']['topic'],
                             json.dumps(self.mqtt_config['output']['utterance_start']['message']))
        logging.debug("Start utterance detection")
        self._vad.detect_utterance(sil_th=int(self.config['SIL_TH']), speech_th=int(self.config['SPEECH_TH']), callback=self._on_utterance_end)
    
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
        self.resume_kws()
    
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
        topics = []
        for key in self.mqtt_config['input'].keys():
            topics.append(self.mqtt_config['input'][key]['topic'])
        for topic in set(topics):
            self._client.subscribe(topic)
            logging.debug("Subscribed to {}".format(topic))
    
    def _on_broker_disconnect(self):
        logging.warning("MQTT Client has been disconnected")
        if self._running:
            self._connect(self.config['MQTT_LOCAL_HOST'], int(self.config['MQTT_LOCAL_PORT']))

    def _on_message(self, client, userdata, message):
        msg = str(message.payload.decode("utf-8"))
        topic = message.topic
        logging.debug("Incoming message ({}): {}".format(topic, msg))
        payload = message.payload.decode("utf-8")
        try:
            content = json.loads(payload)
        except:
            logging.warning("Could not parse input message")
            value = 'any'
        else:
            value = content['value'] if 'value' in content.keys() else 'any' 
        
        self._process_input(topic, value)

    def _on_error(self, err):
        logging.error("Catched Error: {}".format(err))

    def _process_input(self, topic, value):
        if topic in self._action_map.keys():
            if value in self._action_map[topic].keys():
                self._action_map[topic][value]()
            else:
                logging.warning("No action provided for value {} on topic {}".format(value, topic))

    def suspend(self):
        self.cancel_utterance()
        self.pipeline.stop()
        logging.debug("Process is suspended")

    def suspend_kws(self):
        self._kws.stop()
        logging.debug("KWS is suspended")

    def resume(self):
        self.pipeline.resume()
        self._kws.clear_buffer()
        logging.debug("Process is resumed")

    def resume_kws(self):
        self._kws.clear_buffer()
        self._kws.resume()
        logging.debug("KWS is resumed")

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