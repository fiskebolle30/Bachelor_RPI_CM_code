# Bachelor_RPI_CM_code
All raspberry PI Compute module code for the bachelor project

# Project structure
The folder structure of the project is as follows: <br />
src <br />
├── drivers <br />
│   ├── __init.py__ <br />
│   ├── lock.py  <br />
│   └── modem.py  <br />
├── logs  <br />
├── configs  <br />
│   ├── Audiomoth.config <br />
│   └── credentials.json <br />
├── __init.py__ <br />
├── logs.py <br />
├── main.py <br />
└── utils.py <br />

<br />

`src/drivers` contains Python modules the hardware driver for the modem. <br />
`src/logs` is an empty folder where log files will be created <br />
`src/configs` contains the configuration files and credentials for being able to connect to the 4G Network

# Comments
The modem.py file is fully the same as the BUGG uses: https://github.com/bugg-resources/buggd/blob/main/src/buggd/drivers/modem.py

The modem.py file needs the lock.py file to function, and this is also taken directly from the BUGG repo: https://github.com/bugg-resources/buggd/blob/main/src/buggd/drivers/lock.py

The logs.py file is also taken directly from the BUGG repo: https://github.com/bugg-resources/buggd/blob/main/src/buggd/apps/buggd/log.py

The utils.py is a combination of functions already found in the BUGGs utils.py script and functions written specifically for this project. They're divided in the file, with comments on top of each section to show what is written and what is borrowed. 
BUGG repo utils: https://github.com/bugg-resources/buggd/blob/main/src/buggd/apps/buggd/utils.py

# Variables that need changed for functionality
- sd_mount_loc in main.py needs to be updated to the actual usb location
- wav_directory in main.py needs to be updated to the actual path

- connection_retries can be edited if another value is preferred
- shutdown_GPIO_pin can also be changed if the MCU is reading a different one

- credentials.json needs to be filled out with actual network and device credentials
- AudioMoth.config might also need updated if any other settings are preferred (can be created through the app)

# To-dos/future implementations
Telemetry files should be created. So far the get_sys_uptime() function exists which can be used to see how long the RPi is up each time. Functions for battery level and device position also needs added 
