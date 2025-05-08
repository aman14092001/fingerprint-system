#!/usr/bin/env python3.5
# -*- coding:utf-8 -*-

import serial
import time
import sys
import os
from PIL import Image
import re
import datetime
import threading
import sqlite3
import torch
import torch.nn as nn
from torchvision import models, transforms
from collections import OrderedDict

# Constants for capacitive sensor
DATABASE_PATH = "/home/live_finger/newtry27jan/fingerprints_capacitive.db"
save_dir = os.path.expanduser("/home/live_finger/newtry27jan/Fingerprints")

# Command codes
Command = 0xAA55
Response = 0x55AA
Command_SID = 0x00
Response_SID = 0x01
Command_DID = 0x00
Response_DID = 0x00

# Command and Response codes
CMD_FINGER_DETECT = 0x21
CMD_GET_IMAGE = 0x20
CMD_GENERATE = 0x60
CMD_MERGE = 0x61
CMD_STORE_CHAR = 0x40
CMD_GET_EMPTY_ID = 0x45
CMD_SEARCH = 0x63
CMD_UP_IMAGE_CODE = 0x22
CMD_DEL_CHAR = 0x44
CMD_GET_ENROLL_COUNT = 0x48
CMD_GET_ENROLLED_ID_LIST = 0x49

# Result codes
ERR_SUCCESS = 0x00
ERR_FAIL = 0x01
ERR_FP_NOT_DETECTED = 0x28
ERR_INVALID_PARAM = 0x22
ERR_TMPL_EMPTY = 0x12

# Data lengths
DATA_0 = 0x0000
DATA_1 = 0x0001
DATA_2 = 0x0002
DATA_3 = 0x0003
DATA_4 = 0x0004
DATA_6 = 0x0006

# Image dimensions
WIDTH = 242
HEIGHT = 266

# Update model path
MODEL_PATH = "/home/live_finger/newtry27jan/model/bothSensor_combined_model3_may7.pth"

class Cmd_Packet:
    def __init__(self):
        self.PREFIX = 0x0000
        self.SID = 0x00
        self.DID = 0x00
        self.CMD = 0x00
        self.LEN = 0x0000
        self.DATA = [0x00] * 16
        self.CKS = 0x0000

class Rps_Packet:
    def __init__(self):
        self.PREFIX = 0x0000
        self.SID = 0x00
        self.DID = 0x00
        self.CMD = 0x00
        self.LEN = 0x0000
        self.RET = 0x0000
        self.DATA = [0x00] * 14
        self.CKS = 0x0000

class CapSensor:
    def __init__(self, port='/dev/ttyUSB0', baudrate=460800, log_callback=None):
        self.log_callback = log_callback
        try:
            self.ser = serial.Serial(port, baudrate)
            self.cmd = [0x55, 0xAA, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01]
            self.rps = [0x00] * 26
            self.CMD = Cmd_Packet()
            self.RPS = Rps_Packet()
            
            # Initialize command packet with default values
            self.CMD.PREFIX = Command
            self.CMD.SID = Command_SID
            self.CMD.DID = Command_DID
            
            # Remove database reinitialization
            self.initialize_database()
            self.last_match_position = None
            self.is_anti_spoof_enabled = False
            
            # Initialize spoof detection model
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            self.model = self.load_model()
            self.preprocess = transforms.Compose([
                transforms.Resize((280, 280)),
                transforms.TenCrop(224),
                transforms.Lambda(lambda crops: torch.stack([transforms.ToTensor()(crop) for crop in crops])),
                transforms.ConvertImageDtype(torch.float),
                transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
            ])
            
            if self.log_callback:
                self.log_callback("Capacitive sensor initialized successfully.")
            else:
                print("Capacitive sensor initialized successfully.")
        except Exception as e:
            msg = f"Failed to initialize sensor: {e}"
            if self.log_callback:
                self.log_callback(msg)
            else:
                print(msg)
            raise e

    def initialize_database(self):
        try:
            # Create database directory if it doesn't exist
            db_dir = os.path.dirname(DATABASE_PATH)
            if not os.path.exists(db_dir):
                os.makedirs(db_dir, exist_ok=True)
                if self.log_callback:
                    self.log_callback(f"Created database directory: {db_dir}")

            # Create database file if it doesn't exist
            if not os.path.exists(DATABASE_PATH):
                conn = sqlite3.connect(DATABASE_PATH)
                conn.close()
                if self.log_callback:
                    self.log_callback(f"Created new database file: {DATABASE_PATH}")

            # Initialize database schema only if table doesn't exist
            conn = sqlite3.connect(DATABASE_PATH)
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS fingerprints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    template_position INTEGER NOT NULL UNIQUE
                )
            ''')
            conn.commit()
            conn.close()
            if self.log_callback:
                self.log_callback(f"Database initialized successfully at: {DATABASE_PATH}")
        except sqlite3.Error as e:
            msg = f"Database Initialization Failed: {e}"
            if self.log_callback:
                self.log_callback(msg)
            raise e

    def Tx_cmd(self):
        CKS = 0
        self.cmd[0] = self.CMD.PREFIX & 0xff
        self.cmd[1] = (self.CMD.PREFIX & 0xff00) >> 8
        self.cmd[2] = self.CMD.SID
        self.cmd[3] = self.CMD.DID
        self.cmd[4] = self.CMD.CMD
        self.cmd[5] = 0x00
        self.cmd[6] = self.CMD.LEN & 0xff
        self.cmd[7] = (self.CMD.LEN & 0xff00) >> 8
        for i in range(self.CMD.LEN):
            self.cmd[8+i] = self.CMD.DATA[i]
        for i in range(24):
            CKS = CKS + self.cmd[i]
        self.cmd[24] = CKS & 0xff
        self.cmd[25] = (CKS & 0xff00) >> 8
        self.ser.write(self.cmd)

    def Rx_cmd(self, back):
        a = 1
        CKS = 0
        while a:
            while self.ser.inWaiting() > 0:
                for i in range(26):
                    self.rps[i] = ord(self.ser.read())
                a = 0
                if self.rps[4] == 0xff:
                    return 1
                self.Rx_CMD_Process()
                for i in range(24):
                    CKS = (CKS + self.rps[i]) & 0xffff
                if CKS == self.RPS.CKS:
                    return self.Rx_Data_Process(back)
        return 1

    def Rx_CMD_Process(self):
        self.RPS.PREFIX = self.rps[0] + self.rps[1] * 0x100
        self.RPS.SID = self.rps[2]
        self.RPS.DID = self.rps[3]
        self.RPS.CMD = self.rps[4] + self.rps[5] * 0x100
        self.RPS.LEN = self.rps[6] + self.rps[7] * 0x100
        self.RPS.RET = self.rps[8] + self.rps[9] * 0x100
        for i in range(14):
            self.RPS.DATA[i] = self.rps[10 + i]
        self.RPS.CKS = self.rps[24] + self.rps[25] * 0x100

    def Rx_Data_Process(self, back):
        if self.RPS.CMD == CMD_FINGER_DETECT:
            return self.RpsFingerDetect(back)
        elif self.RPS.CMD == CMD_GET_IMAGE:
            return self.RpsGetImage(back)
        elif self.RPS.CMD == CMD_GENERATE:
            return self.RpsGenerate(back)
        elif self.RPS.CMD == CMD_MERGE:
            return self.RpsMerge(back)
        elif self.RPS.CMD == CMD_STORE_CHAR:
            return self.RpsStoreCher(back)
        elif self.RPS.CMD == CMD_GET_EMPTY_ID:
            return self.RpsGetEmptyID(back)
        elif self.RPS.CMD == CMD_SEARCH:
            return self.RpsSearch(back)
        elif self.RPS.CMD == CMD_DEL_CHAR:
            return self.RpsDelChar(back)
        elif self.RPS.CMD == CMD_GET_ENROLL_COUNT:
            return self.RpsGetEnrollCount(back)
        elif self.RPS.CMD == CMD_GET_ENROLLED_ID_LIST:
            return self.RpsGetEnrolledIdList(back)
        return 1

    def enroll_finger(self, name, update_ui_callback=None, enroll_complete_callback=None):
        try:
            if update_ui_callback:
                update_ui_callback("🔄 Starting fingerprint enrollment...")

            # Get empty ID
            self.CMD.CMD = CMD_GET_EMPTY_ID
            self.CMD.LEN = DATA_4
            self.CMD.DATA[0] = 0x01
            self.CMD.DATA[1] = 0x00
            self.CMD.DATA[2] = 0xB8
            self.CMD.DATA[3] = 0x0B
            self.Tx_cmd()
            self.Rx_cmd(1)
            k = self.RPS.DATA[0] + self.RPS.DATA[1] * 0x0100

            # Fingerprint enrollment process
            for a in range(3):
                if update_ui_callback:
                    update_ui_callback(f"🔄 Step {a+1}/3: Place your finger on the sensor")
                
                for i in range(3):
                    if not self.CmdFingerDetect(1):
                        if update_ui_callback:
                            update_ui_callback(f"⚠️ Step {a+1}/3: Remove your finger")
                    while not self.CmdFingerDetect(1):
                        time.sleep(0.01)
                    if update_ui_callback:
                        update_ui_callback(f"🔄 Step {a+1}/3: Press your finger firmly")
                    while self.CmdFingerDetect(1):
                        time.sleep(0.01)
                    if not self.CmdFingerDetect(1):
                        if not self.CmdGetImage(1):
                            if not self.CmdGenerate(a, 1):
                                # Save fingerprint image
                                image_data = self.CmdUpImageCode(1)
                                if image_data:
                                    image_path = self.save_fingerprint_image(image_data, "enroll", k)
                                    if enroll_complete_callback:
                                        enroll_complete_callback(image_path)
                                    if update_ui_callback:
                                        update_ui_callback(f"✅ Step {a+1}/3: Fingerprint captured successfully")
                                break

            if i == 2:
                if update_ui_callback:
                    update_ui_callback("❌ Enrollment failed. Please try again.")
                return 1

            # Merge and store the fingerprint
            if update_ui_callback:
                update_ui_callback("🔄 Processing fingerprint data...")
                
            # Add delay before merge
            time.sleep(0.5)
            
            merge_result = self.CmdMerge(0, 3, 1)
            if merge_result == ERR_SUCCESS:
                # Add delay before store
                time.sleep(0.5)
                
                store_result = self.CmdStoreChar(k, 0, 1)
                if store_result == ERR_SUCCESS:
                    # Save to database first
                    try:
                        db = sqlite3.connect(DATABASE_PATH)
                        cursor = db.cursor()
                        cursor.execute('INSERT INTO fingerprints (name, template_position) VALUES (?, ?)', (name, k))
                        db.commit()
                        db.close()
                        
                        if update_ui_callback:
                            update_ui_callback(f"✅ Enrollment successful for {name}")
                        return 0
                    except sqlite3.Error as db_error:
                        if update_ui_callback:
                            update_ui_callback(f"❌ Database error: {db_error}")
                        # Try to delete the template from sensor since database save failed
                        self.CmdDelChar(k, 0, 0)
                        raise db_error
                else:
                    if update_ui_callback:
                        update_ui_callback("❌ Failed to store template in sensor. Error code: " + str(store_result))
                    return 1
            else:
                if update_ui_callback:
                    update_ui_callback("❌ Failed to merge templates. Error code: " + str(merge_result))
                return 1

        except Exception as e:
            if update_ui_callback:
                update_ui_callback(f"❌ Enrollment Failed: {e}")
            raise e

    def search_finger(self, update_ui_callback=None, search_complete_callback=None):
        def run_search():
            search_start_time = time.time()
            try:
                # Check if port is open and working
                if not self.ser.is_open:
                    if update_ui_callback:
                        update_ui_callback("❌ Sensor port is not open. Attempting to reconnect...")
                    try:
                        self.ser.open()
                    except Exception as e:
                        if update_ui_callback:
                            update_ui_callback(f"❌ Failed to reconnect to sensor: {e}")
                        return

                if update_ui_callback:
                    update_ui_callback("🔄 Waiting for finger...")

                # Hardcoded start and end addresses
                data_start = 1
                data_end = 3000
                data = data_start * 0x10000 + data_end
                k = (data & 0xffff0000) >> 16
                n = data & 0xffff

                # Capture fingerprint
                for i in range(3):
                    try:
                        if not self.CmdFingerDetect(1):
                            if update_ui_callback:
                                update_ui_callback("⚠️ Please move your finger away")
                        while not self.CmdFingerDetect(1):
                            time.sleep(0.01)
                        if update_ui_callback:
                            update_ui_callback("🔄 Please press your finger")
                        while self.CmdFingerDetect(1):
                            time.sleep(0.01)
                        if not self.CmdFingerDetect(1):
                            if not self.CmdGetImage(1):
                                if not self.CmdGenerate(0, 1):
                                    # Save fingerprint image
                                    image_data = self.CmdUpImageCode(1)
                                    if image_data:
                                        image_path = self.save_fingerprint_image(image_data, "search")
                                        
                                        # Perform spoof detection if enabled
                                        spoof_status = "Disabled"
                                        spoof_detection_time = 0
                                        if self.is_anti_spoof_enabled:
                                            if update_ui_callback:
                                                update_ui_callback("🔄 Performing spoof detection...")
                                            spoof_detection_start = time.time()
                                            spoof_status = self.spoof_detection_algorithm(image_path)
                                            spoof_detection_time = time.time() - spoof_detection_start
                                            if update_ui_callback:
                                                update_ui_callback(f"✅ Spoof detection completed in {spoof_detection_time:.2f} seconds")
                                        
                                        if search_complete_callback:
                                            search_complete_callback(True, image_path, spoof_status)
                                    break
                    except serial.SerialException as e:
                        if update_ui_callback:
                            update_ui_callback(f"❌ Serial communication error: {e}")
                        # Try to recover the connection
                        try:
                            self.ser.close()
                            time.sleep(1)  # Wait before reconnecting
                            self.ser.open()
                            if update_ui_callback:
                                update_ui_callback("✅ Sensor reconnected successfully")
                        except Exception as e:
                            if update_ui_callback:
                                update_ui_callback(f"❌ Failed to recover sensor connection: {e}")
                            return

                if i == 2:
                    if update_ui_callback:
                        update_ui_callback("❌ Fingerprint capture failed")
                    return 1

                # Search for match
                if update_ui_callback:
                    update_ui_callback("🔄 Searching database...")
                    
                search_start = time.time()
                self.CMD.CMD = CMD_SEARCH
                self.CMD.LEN = DATA_6
                self.CMD.DATA[0] = 0x00
                self.CMD.DATA[1] = 0x00
                self.CMD.DATA[2] = k & 0xff
                self.CMD.DATA[3] = (k & 0xff00) >> 8
                self.CMD.DATA[4] = n & 0xff
                self.CMD.DATA[5] = (n & 0xff00) >> 8
                self.Tx_cmd()
                result = self.Rx_cmd(0)
                search_time = time.time() - search_start
                
                if result == ERR_SUCCESS:
                    self.last_match_position = self.RPS.DATA[0] + self.RPS.DATA[1] * 0x0100
                    
                    # Get the name of the matched fingerprint
                    matched_name = None
                    try:
                        db = sqlite3.connect(DATABASE_PATH)
                        cursor = db.cursor()
                        cursor.execute('SELECT name FROM fingerprints WHERE template_position = ?', (self.last_match_position,))
                        result = cursor.fetchone()
                        if result:
                            matched_name = result[0]
                            if update_ui_callback:
                                update_ui_callback(f"✅ Match found! (Search time: {search_time:.2f} seconds)")
                                update_ui_callback(f"✅ Fingerprint matched with: {matched_name}")
                        db.close()
                    except Exception as e:
                        if update_ui_callback:
                            update_ui_callback(f"❌ Database error: {str(e)}")
                            
                    if search_complete_callback:
                        search_complete_callback(True, image_path, spoof_status, matched_name)
                else:
                    if update_ui_callback:
                        update_ui_callback(f"❌ No match found (Search time: {search_time:.2f} seconds)")
                    if search_complete_callback:
                        search_complete_callback(False, image_path, spoof_status, None)

                # Calculate total time
                total_search_time = time.time() - search_start_time
                if update_ui_callback:
                    update_ui_callback(f"⏱️ Total operation time: {total_search_time:.2f} seconds")

            except Exception as e:
                if update_ui_callback:
                    update_ui_callback(f"❌ Search Failed: {e}")
                # Try to recover the connection
                try:
                    if self.ser.is_open:
                        self.ser.close()
                    time.sleep(1)  # Wait before reconnecting
                    self.ser.open()
                    if update_ui_callback:
                        update_ui_callback("✅ Sensor reconnected successfully")
                except Exception as e:
                    if update_ui_callback:
                        update_ui_callback(f"❌ Failed to recover sensor connection: {e}")

        threading.Thread(target=run_search).start()

    def delete_finger(self, position, update_ui_callback=None):
        try:
            if update_ui_callback:
                update_ui_callback("🔄 Checking fingerprint database...")
                
            db = sqlite3.connect(DATABASE_PATH)
            cursor = db.cursor()
            cursor.execute('SELECT name, template_position FROM fingerprints WHERE id = ?', (position,))
            result = cursor.fetchone()

            if not result:
                if update_ui_callback:
                    update_ui_callback(f"❌ No fingerprint found at position {position}.")
                return False

            name, template_position = result

            if update_ui_callback:
                update_ui_callback("🔄 Deleting fingerprint from sensor...")

            # Initialize command packet
            self.CMD.PREFIX = Command
            self.CMD.SID = Command_SID
            self.CMD.DID = Command_DID
            self.CMD.CMD = CMD_DEL_CHAR
            self.CMD.LEN = DATA_4
            # Set the start and end ID to the same value (delete only one ID)
            self.CMD.DATA[0] = template_position & 0xff
            self.CMD.DATA[1] = (template_position & 0xff00) >> 8
            self.CMD.DATA[2] = template_position & 0xff
            self.CMD.DATA[3] = (template_position & 0xff00) >> 8
            self.Tx_cmd()
            
            if self.Rx_cmd(1) == ERR_SUCCESS:
                cursor.execute('DELETE FROM fingerprints WHERE id = ?', (position,))
                db.commit()
                if update_ui_callback:
                    update_ui_callback(f"✅ Fingerprint for {name} deleted successfully.")
                return True
            else:
                if update_ui_callback:
                    update_ui_callback("❌ Failed to delete template from the sensor.")
                return False

        except Exception as e:
            if update_ui_callback:
                update_ui_callback(f"❌ Failed to delete fingerprint: {e}")
            return False
        finally:
            db.close()

    def list_enrolled_fingers(self):
        try:
            db = sqlite3.connect(DATABASE_PATH)
            cursor = db.cursor()
            cursor.execute('SELECT name FROM fingerprints')
            fingers = cursor.fetchall()
            return [finger[0] for finger in fingers]
        except Exception as e:
            msg = f"Failed to fetch enrolled fingerprints: {e}"
            if self.log_callback:
                self.log_callback(msg)
            else:
                print(msg)
            raise e
        finally:
            db.close()

    def save_fingerprint_image(self, image_data, operation_type, id=None):
        if image_data is None:
            return None
        
        # Generate timestamp for unique filename
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Create filename based on operation type and ID
        if operation_type == "enroll" and id is not None:
            filename = f"fingerprint_images/enroll/fp_id_{id}_{timestamp}.txt"
            image_filename = f"fingerprint_images/enroll/fp_id_{id}_{timestamp}.bmp"
        elif operation_type == "search":
            filename = f"fingerprint_images/search/search_{timestamp}.txt"
            image_filename = f"fingerprint_images/search/search_{timestamp}.bmp"
        else:
            filename = f"fingerprint_images/{operation_type}/{operation_type}_{timestamp}.txt"
            image_filename = f"fingerprint_images/{operation_type}/{operation_type}_{timestamp}.bmp"
        
        # Save raw data to text file
        self.Data_Txt(image_data, filename)
        
        # Convert and save as image
        pixel_data = self.read_data_txt(filename)
        self.data_to_image(pixel_data, WIDTH, HEIGHT, image_filename)
        
        return image_filename

    def Data_Txt(self, Rx_data, filename):
        output = open(filename, 'w', encoding='gbk')
        i = 38
        for j in range(129):
            for o in range(8):
                for p in range(62):
                    output.write("0x%x," % Rx_data[i])
                    i = i + 1
                output.write('\n')
            i = i + 14
        for j in range(6):
            for p in range(62):
                output.write("0x%x," % Rx_data[i])
                i = i + 1
            output.write('\n')
        for p in range(8):
            output.write("0x%x," % Rx_data[i])
            i = i + 1
        if self.log_callback:
            self.log_callback(f"Data written to {filename}")
        else:
            print(f"Data written to {filename}")

    def read_data_txt(self, filename):
        pixel_data = []
        with open(filename, 'r', encoding='gbk') as file:
            for line in file:
                hex_values = re.findall(r'0x[0-9a-fA-F]+', line)
                for hex_val in hex_values:
                    pixel_data.append(int(hex_val, 16))
        return pixel_data

    def data_to_image(self, pixel_data, width, height, output_filename):
        img = Image.new('L', (width, height))
        pixels = img.load()
        data_index = 0
        for y in range(height):
            for x in range(width):
                if data_index < len(pixel_data):
                    pixels[x, y] = pixel_data[data_index]
                    data_index += 1
                else:
                    pixels[x, y] = 0
        img.save(output_filename)
        if self.log_callback:
            self.log_callback(f"Image saved as {output_filename}")
        else:
            print(f"Image saved as {output_filename}")

    def toggle_anti_spoof(self):
        """Toggle anti-spoof detection"""
        self.is_anti_spoof_enabled = not self.is_anti_spoof_enabled
        if self.log_callback:
            self.log_callback(f"Anti-spoof detection {'enabled' if self.is_anti_spoof_enabled else 'disabled'}")
        else:
            print(f"Anti-spoof detection {'enabled' if self.is_anti_spoof_enabled else 'disabled'}")

    def __del__(self):
        """Cleanup when object is destroyed."""
        try:
            if hasattr(self, 'ser') and self.ser.is_open:
                self.ser.close()
            if hasattr(self, 'model'):
                del self.model
        except Exception as e:
            msg = f"Error during cleanup: {e}"
            if self.log_callback:
                self.log_callback(msg)
            else:
                print(msg)

    def RpsFingerDetect(self, back):
        if back:
            if not self.RPS.RET:
                return not self.RPS.DATA[0]
        else:
            if self.RPS.RET:
                if self.log_callback:
                    self.log_callback("Instruction processing failure\r\n")
            else:
                if self.RPS.DATA[0]:
                    if self.log_callback:
                        self.log_callback("We got a print on it\r\n")
                else:
                    if self.log_callback:
                        self.log_callback("No prints were detected\r\n")
                return not self.RPS.DATA[0]
        return 2

    def RpsGetImage(self, back):
        return self.RPS.RET

    def RpsGenerate(self, back):
        return self.RPS.RET

    def RpsMerge(self, back):
        return self.RPS.RET

    def RpsStoreCher(self, back):
        return self.RPS.RET

    def RpsGetEmptyID(self, back):
        return self.RPS.RET

    def RpsSearch(self, back):
        if back:
            return self.RPS.RET
        else:
            if self.RPS.RET:
                if self.log_callback:
                    self.log_callback("Fingerprint not found in the specified range\r\n")
            else:
                data = self.RPS.DATA[0] + self.RPS.DATA[1] * 0x0100
                if self.log_callback:
                    self.log_callback("Successful fingerprint match found!\r\n")
                    self.log_callback("The matching fingerprint ID is: %d \r\n" % data)
            return self.RPS.RET

    def RpsDelChar(self, back):
        if back:
            return self.RPS.RET
        else:
            if self.RPS.RET == ERR_SUCCESS:
                if self.log_callback:
                    self.log_callback("Fingerprint deleted successfully\r\n")
            elif self.RPS.RET == ERR_FAIL:
                if self.log_callback:
                    self.log_callback("Instruction processing failure\r\n")
            elif self.RPS.RET == ERR_INVALID_PARAM:
                if self.log_callback:
                    self.log_callback("Specified ID is invalid\r\n")
            elif self.RPS.RET == ERR_TMPL_EMPTY:
                if self.log_callback:
                    self.log_callback("No fingerprint registered at the specified ID\r\n")
            else:
                if self.log_callback:
                    self.log_callback("Unknown error occurred\r\n")
            return self.RPS.RET

    def RpsGetEnrollCount(self, back):
        if back:
            return self.RPS.RET
        else:
            if self.RPS.RET:
                if self.log_callback:
                    self.log_callback("Instruction processing failure\r\n")
            else:
                data = self.RPS.DATA[0] + self.RPS.DATA[1] * 0x0100
                if self.log_callback:
                    self.log_callback("Total number of registered fingerprints: %d \r\n" % data)
            return self.RPS.RET

    def RpsGetEnrolledIdList(self, back):
        if back:
            return self.RPS.RET
        else:
            if self.RPS.RET:
                if self.log_callback:
                    self.log_callback("Instruction processing failure\r\n")
            else:
                # The actual ID list comes in a separate data packet
                # We need to read the data packet after the response packet
                data_packet = []
                time.sleep(0.1)  # Give some time for the data packet to arrive
                
                # Read all available data
                while self.ser.inWaiting() > 0:
                    data_packet.append(ord(self.ser.read()))
                
                if len(data_packet) < 10:
                    if self.log_callback:
                        self.log_callback("Data packet too short\r\n")
                    return self.RPS.RET
                    
                # Extract the ID list data (all bytes after the header)
                id_list = data_packet[10:]
                
                enrolled_ids = []
                # Only iterate through the actual length of the data
                for byte_idx in range(len(id_list)):
                    byte = id_list[byte_idx]
                    for bit_idx in range(8):
                        if byte & (1 << bit_idx):
                            id = byte_idx * 8 + bit_idx
                            if id <= 3000:
                                enrolled_ids.append(id)
                
                if enrolled_ids:
                    if self.log_callback:
                        self.log_callback("Enrolled Fingerprint IDs:", enrolled_ids)
                        self.log_callback(f"Total enrolled fingerprints: {len(enrolled_ids)}")
                    else:
                        print("Enrolled Fingerprint IDs:", enrolled_ids)
                        print(f"Total enrolled fingerprints: {len(enrolled_ids)}")
                else:
                    if self.log_callback:
                        self.log_callback("No enrolled fingerprints found")
                    else:
                        print("No enrolled fingerprints found")
            return self.RPS.RET

    def CmdFingerDetect(self, back):
        self.CMD.CMD = CMD_FINGER_DETECT
        self.CMD.LEN = DATA_0
        self.Tx_cmd()
        return self.Rx_cmd(back)

    def CmdGetImage(self, back):
        self.CMD.CMD = CMD_GET_IMAGE
        self.CMD.LEN = DATA_0
        self.Tx_cmd()
        return self.Rx_cmd(back)

    def CmdGenerate(self, k, back):
        self.CMD.CMD = CMD_GENERATE
        self.CMD.LEN = DATA_2
        self.CMD.DATA[0] = k 
        self.CMD.DATA[1] = 0x00 
        self.Tx_cmd()
        return self.Rx_cmd(back)

    def CmdMerge(self, k, n, back):
        self.CMD.CMD = CMD_MERGE
        self.CMD.LEN = DATA_3
        self.CMD.DATA[0] = k 
        self.CMD.DATA[1] = 0x00 
        self.CMD.DATA[2] = n 
        self.Tx_cmd()
        return self.Rx_cmd(back)

    def CmdStoreChar(self, k, n, back):
        self.CMD.CMD = CMD_STORE_CHAR
        self.CMD.LEN = DATA_4
        self.CMD.DATA[0] = k 
        self.CMD.DATA[1] = 0x00 
        self.CMD.DATA[2] = n 
        self.CMD.DATA[3] = 0x00 
        self.Tx_cmd()
        return self.Rx_cmd(back)

    def CmdUpImageCode(self, back):
        Rx_data = []
        if not self.CmdFingerDetect(back):
            if self.log_callback:
                self.log_callback("Please move your finger away")
        while not self.CmdFingerDetect(back):
            time.sleep(0.01)
        if self.log_callback:
            self.log_callback("Please press your finger")
        while self.CmdFingerDetect(back):
            time.sleep(0.01)
        if not self.CmdFingerDetect(back):
            if not self.CmdGetImage(back):
                if self.log_callback:
                    self.log_callback("Please wait while data is being received")
                self.CMD.CMD = CMD_UP_IMAGE_CODE
                self.CMD.LEN = DATA_1
                self.CMD.DATA[0] = 0x00 
                self.Tx_cmd()
                time.sleep(0.1)
                while self.ser.inWaiting() > 0:
                    for i in range(66218):
                        Rx_data.append(ord(self.ser.read()))
                return Rx_data
        return None

    def GetEnrolledIdList(self, back):
        self.CMD.CMD = CMD_GET_ENROLLED_ID_LIST
        self.CMD.LEN = DATA_0
        self.Tx_cmd()
        return self.Rx_cmd(not back)

    def GetUserCount(self, back):
        self.CMD.CMD = CMD_GET_ENROLL_COUNT
        self.CMD.LEN = DATA_4
        self.CMD.DATA[0] = 0x01
        self.CMD.DATA[1] = 0x00
        self.CMD.DATA[2] = 0xB8
        self.CMD.DATA[3] = 0x0B
        self.Tx_cmd()
        return self.Rx_cmd(not back)

    def load_model(self):
        """Load the pre-trained spoof detection model."""
        try:
            # Initialize EfficientNet model
            model = models.efficientnet_b0(pretrained=True)
            model.classifier[1] = nn.Linear(model.classifier[1].in_features, 2)
            model = model.to(self.device)

            # Load state dict
            state_dict = torch.load(MODEL_PATH, map_location=self.device)
            model.load_state_dict(state_dict)
            model.eval()
            if self.log_callback:
                self.log_callback("Spoof detection model loaded successfully.")
            return model
        except Exception as e:
            msg = f"Failed to load spoof detection model: {e}"
            if self.log_callback:
                self.log_callback(msg)
            else:
                print(msg)
            return None

    def spoof_detection_algorithm(self, image_path):
        """Check if the fingerprint is LIVE or FAKE."""
        try:
            if not self.model:
                return "Model not loaded"

            # Load and preprocess image
            image = Image.open(image_path).convert("RGB")
            
            # Apply the same transforms as in training
            preprocess = transforms.Compose([
                transforms.Resize(256),
                transforms.CenterCrop(224),
                transforms.ToTensor(),
                transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
            ])
            
            image_tensor = preprocess(image)
            image_tensor = image_tensor.unsqueeze(0).to(self.device)

            # Make prediction
            with torch.no_grad():
                outputs = self.model(image_tensor)
                _, preds = torch.max(outputs, 1)
                result = "FAKE" if preds[0] == 0 else "LIVE"

            return result
        except Exception as e:
            msg = f"Error in spoof detection: {e}"
            if self.log_callback:
                self.log_callback(msg)
            else:
                print(msg)
            return "Error"

    def read_data(self):
        """Read data from the sensor"""
        try:
            if self.CmdFingerDetect(1):
                return "Finger detected"
            return None
        except Exception as e:
            msg = f"Error reading data: {e}"
            if self.log_callback:
                self.log_callback(msg)
            else:
                print(msg)
            return None

    def cleanup(self):
        """Clean up resources"""
        try:
            if hasattr(self, 'ser') and self.ser.is_open:
                self.ser.close()
            if hasattr(self, 'model'):
                del self.model
        except Exception as e:
            msg = f"Error during cleanup: {e}"
            if self.log_callback:
                self.log_callback(msg)
            else:
                print(msg)

# Example usage
if __name__ == "__main__":
    sensor = CapSensor()
    sensor.enroll_finger("test_user", lambda x: print(x), lambda x: print(f"Enroll complete: {x}")) 