# LinTO-command-module
![version](https://img.shields.io/github/manifest-json/v/linto-ai/linto-command-module)
![release](https://img.shields.io/github/v/release/linto-ai/linto-command-module.png)

LinTO Command Module purpose is to:
* Spot wake-up-word
* Detect beginning and end of voice command utterances.

## Introduction
linto-command module is a software brick for the LinTO Maker prototype. It is designed originaly to run on raspeberry pi 3 within LinTO-OS.

Its relies on several tiers libraries to acquire the audio from the microphone, detect voice activity, extract signal features and detect keyword using Tensorflow or Keras RNN models.
__________________
## Getting Started
The easiest way to use linto-command-module is to download the binary package.

### Binary package
The module has been tested on x86-64 and armv7l, binary archives can be downloaded there:
* x86_64: [Here](https://github.com/linto-ai/linto-command-module/releases/download/v0.1/linto-command-0.1-x64.tar.gz)
* Armv7l: [Here](https://github.com/linto-ai/linto-command-module/releases/download/v0.1/linto-command-0.1-armv7l.tar.gz)

Extract the folder:
```bash
ARCH=armv7l
VERSION=0.2
tar xzf linto-command_$VERSION_$ARCH.tar.gz
```

### From Source
1. Download the repository
```bash
git clone https://github.com/linto-ai/linto-command-module.git
cd linto-command-module
```

2. Install dependencies (w/ or w/out using virtualenv)
```bash
MY_VENV_FOLDER=path/to/your/venv
virtualenv -p /usr/bin/python3.X --no-site-package $MY_VENV_FOLDER
source $MY_VENV_FOLDER/bin/activate
pip install -r requirements.txt
```
__________________
## Usage
* Executable from binary: EXTRACTED_FOLDER_LOCATION/command/command
* Executable from source: REPO/command/command.py

```
usage: command[.py] [-h] [--debug]

Voice Command Module

optional arguments:

  -h, --help  show this help message and exit

  --debug     Prompts debug
```

The executable comes alongside a .env_default file that contains its parameters.
You can override those parameters using a .env file and you can overide those formers by using environment variables.

Exemple:
```
echo MODEL_FOLDER=/my/custom_model/path > .env && ./command --debug
                            or
MODEL_FOLDER=/my/custom_model/path ./command --debug
                            or
export MODEL_FOLDER=/my/custom_model/path && ./command --debug
```
**Parameters are:**
* MODEL_FOLDER: path to a folder containing a .net or .pb file alongside its .param file (default ~/model/kws)
* MQTT_LOCAL_HOST: MQTT local broker address (default localhost)
* MQTT_LOCAL_PORT: MQTT local broker port (default 1883)
* KWS_TH: Threshold for keyword detection (default 0.5)
__________________
## Built using

* [PyRTSTools]() - Custom tools for realtime speech processing.
* [Tenacity](https://github.com/jd/tenacity) - General-purpose retrying library
* [paho-mqtt](https://pypi.org/project/paho-mqtt/) - MQTT client library.


## License

This project is licensed under the GNU AFFERO License - see the [LICENSE.md](LICENSE.md) file for details.
