""" Provides power control and status information for the RC7620 modem """
import logging
import time
from enum import Enum, auto
import usb.core
import usb.util
import os
import serial
import RPi.GPIO as GPIO
from .lock import Lock

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

P3V7_EN = 7
POWER_ON_N = 5
RESET_IN_N = 6

LOCK_FILE = "/tmp/modem.lock"

CONTROL_INTERFACE = "/dev/tty_modem_command_interface"
CONTROL_INTERFACE_BAUD = 115200
CONTROL_INTERFACE_TIMEOUT = 1
CONTROL_INTERFACE_READ_SIZE = 100
TIME_WAIT_RESPONSE = 0.5

VENDOR_ID = 0x1199
PRODUCT_ID = 0x68c0

class ModemInUseException(Exception):
    """Exception raised when the modem is already in use by another process."""

class Modem:
    """
    Provides power control and status information for the RC7620 GSM modem
    We use the FileLock library to ensure that only one instance of the driver is running at a time.

    """

    def __init__(self, lock_file_path=LOCK_FILE):
        """ Attempt to acquire the lock and initialise the GPIO """
        try:
            self.lock = Lock(lock_file_path)
        except RuntimeError as e:
            logger.critical(e)
            self.result = False
            raise
        self.port = None

        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False) # Squash warning if the pin is already in use

        if self.is_enumerated():
            self.configure_gpio()   # GPIO pins must be HIGH-Z until modem is booted

        GPIO.setup(P3V7_EN, GPIO.OUT)
        GPIO.setup(POWER_ON_N, GPIO.OUT, initial=GPIO.LOW)

        logger.info("Modem driver initialized successfully.")

    def __del__(self):
        """ Release the lock file when the object is deleted"""
        self.release_gpio()
        self.lock.release_lock()

    def configure_gpio(self):
        """
        Configure the GPIO pins
        It's important to only take the pins out of HIGH-Z when the modem is fully booted.
        """
        GPIO.setup(RESET_IN_N, GPIO.OUT, initial=GPIO.LOW)   

    def release_gpio(self):
        """ Set GPIO pins HIGH-Z """
        GPIO.setmode(GPIO.BCM)  # In case release is called before configure
        GPIO.setup(RESET_IN_N, GPIO.IN)

    def turn_on_rail(self):
        """ Turn on the 3.7V rail """
        logger.info("Turning on 3.7V rail.")
        GPIO.output(P3V7_EN, GPIO.HIGH)
    
    def turn_off_rail(self):
        """ Turn off the 3.7V rail """
        logger.info("Turning off 3.7V rail.")
        GPIO.output(P3V7_EN, GPIO.LOW)
        self.release_gpio()


    def rail_is_on(self):
        """
        Check if the 3.7V rail is on.
        This is true if P3V7_EN is an output and is HIGH.
        Calling GPIO.input on an output pin will return the output state.
        """
        return GPIO.gpio_function(P3V7_EN) == GPIO.OUT and GPIO.input(P3V7_EN) == GPIO.HIGH

    def power_on(self):
        """ Turn on the rail, assert POWER_ON_N, and wait for the modem to enumerate """
        if self.is_enumerated():
            logger.info("Modem is already powered on.")
            return True

        self.turn_on_rail()
        time.sleep(0.5)

        # Strobe the POWER_ON_N pin to boot the modem
        GPIO.output(POWER_ON_N, GPIO.HIGH)
        time.sleep(1)
        GPIO.output(POWER_ON_N, GPIO.LOW)

        logger.info("POWER_ON_N asserted, waiting for modem to boot up...")
        # Wait for the modem to boot up
        time.sleep(2)
        max_tries = 10
        while(max_tries > 0):
            logger.info("Checking if modem is enumerated...")
            if self.is_enumerated():
                logger.info("Modem is enumerated.")
                return True
            time.sleep(2)
            max_tries -= 1
        
        logger.error("Timed out waiting for modem to boot up.")
        return False

    def wait_power_off(self):
        """
        Wait for the modem to power down.
        Returns True if the modem has powered down, False otherwise.
        """
        # Wait for the modem to power down 
        max_tries = 10
        while(max_tries > 0):
            if self.is_enumerated():
                logger.info("Modem is still powered on. Waiting for it to power down...")
            else:
                logger.info("Modem has powered down.")
                return True
            time.sleep(3)
            max_tries -= 1
        return False

    def power_off(self):
        """ Command modem to power down safely from software then remove power """
        if not self.is_enumerated():
            logger.info("Modem is already powered off.")
            return True

        logger.info("Turning off modem. Issuing AT command to power down...")
        if self.send_at_command_no_response("AT!POWERDOWN"):
            self.release_gpio()

        if self.wait_power_off():
            self.turn_off_rail()
            return True
        else:
            logger.error("Timed out waiting for modem to power down. Performing emergency power down.")
            self.configure_gpio()
            GPIO.output(RESET_IN_N, GPIO.HIGH)
            time.sleep(10)
            GPIO.output(RESET_IN_N, GPIO.LOW)
            self.release_gpio()

        if self.wait_power_off():
            self.turn_off_rail()
            return True
        else:
            logger.error("Timed out waiting for emergency power down. Turning off 3.7V rail anyway.")
            self.turn_off_rail()
            return False

    def is_enumerated(self):
        """ Check if the modem is enumerated on the USB bus """
        return usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID) is not None


    def is_serial_port_in_use(self, port):
        # Normalize the target device path (resolve any symlinks)
        target_device = os.path.realpath(port)
        
        # Iterate over all processes in /proc
        for pid in os.listdir('/proc'):
            if pid.isdigit():
                fds_path = f'/proc/{pid}/fd'
                try:
                    # List all file descriptors for the current process
                    fds = os.listdir(fds_path)
                except PermissionError:
                    # Skip this process if we don't have permission to view its fds
                    continue
                except FileNotFoundError:
                    # Process might have ended; move on to the next
                    continue
                
                for fd in fds:
                    fd_path = os.path.join(fds_path, fd)
                    try:
                        # Check if this fd is a symlink to our target device
                        if os.path.realpath(fd_path) == target_device:
                            logger.info(f"Device {port} is in use by process {pid}")
                            return True
                    except FileNotFoundError:
                        # The fd was closed; move on to the next
                        continue
        return False

    def send_at_command_no_response(self, command):
        """
        Sends AT command in a fire-and-forget manner.
        
        This is useful if port is already open in non-exclusive mode by another process
        and we'll be unable to read the response.
        """
        try:
            # Open the serial port
            with serial.Serial(CONTROL_INTERFACE, CONTROL_INTERFACE_BAUD, timeout=CONTROL_INTERFACE_TIMEOUT) as ser:
                ser.write((command + "\r\n").encode())

        except serial.SerialException as e:
            logger.error("Failed to send AT command: %s", e)
            return None 

    
    def send_at_command(self, command):
        """
        Sends an AT command to a modem and returns the response.

        Returns:
            list: The response from the modem.
        """
        response = bytes()


        try:
            # Check port isn't already open. Some processes, like ModemManager, open in non-exclusive mode that pyserial can't detect
            if self.is_serial_port_in_use(CONTROL_INTERFACE):
                raise ModemInUseException("Serial port already open")

            # Open the serial port
            with serial.Serial(CONTROL_INTERFACE, CONTROL_INTERFACE_BAUD, timeout=CONTROL_INTERFACE_TIMEOUT) as ser:
                ser.write(("ATE0\r\n").encode())
                time.sleep(0.1)
                ser.read_all()
                time.sleep(0.5)
                ser.write((command + "\r\n").encode())
                time.sleep(0.5)
                response = ser.read_all()

        except serial.SerialException as e:
            logger.error("Failed to send AT command: %s", e)
            return None 

        logger.debug("AT command: %s, response: %s", command, response)
        s = response.decode('utf-8')
        lines = s.splitlines()
        filtered_lines = [line for line in lines if line.strip()] 

        logger.debug(filtered_lines)
        return filtered_lines
   
    def is_responding(self):
        """
        Check if the modem is responding to AT commands.
        
        Returns:
        - True if the modem is responding, False otherwise.
        """
        try:
            response = self.send_at_command("AT")
            return "OK" in response
        except:
            return False

    def get_rssi(self):
        """
        Get the signal strength of the modem.
        
        Returns:
        - The signal strength. If 99, there is no signal.
        - None if the modem does not respond to the AT command.
        """
        response = self.send_at_command("AT+CSQ")

        if response is None:
            return None

        logger.debug("Signal strength response: %s", response)

        for item in response:
            if "+CSQ" in item:
                try:
                    rssi = int(item.split(": ")[1].split(",")[0])
                    if rssi == 99:
                        logger.warning("No signal - missing antenna?")
                    return rssi
                except:
                    return None

    def get_rssi_dbm(self):
        """
        Get the signal strength of the modem in dBm.
        
        Returns:
        - The signal strength in dBm.
        - None if the modem does not respond to the AT command.
        """
        rssi = self.get_rssi()

        if rssi == 99:
            return None 

        if rssi is not None:
            return -113 + 2 * rssi
        return None

    def get_sim_ccid(self):
        """
        Get the SIM card's ICCID.
        
        Returns:
        - The ICCID string.
        - None if the modem does not respond to the AT command or if the SIM card is not present.
        """
        response = self.send_at_command("AT+CCID?")

        if response is None:
            return None

        if "ERROR" in response:
            return None

        logger.debug("SIM CCID response: %s", response)

        for item in response:
            if "+CCID" in item:
                try:
                    return int(item.split(": ")[1])
                except:
                    return None



    def sim_present(self):
        """
        Check if a SIM card is present in the modem.
        
        Returns:
        - True if a SIM card is present, False otherwise.
        """
        return self.get_sim_ccid() is not None