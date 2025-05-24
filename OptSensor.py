import time
import sqlite3
from pyfingerprint.pyfingerprint import PyFingerprint
import torch
import torch.nn as nn
from torchvision import models, transforms
from PIL import Image
from collections import OrderedDict
import os
import threading
import serial
import struct

# Constants for fingerprint sensor
FINGERPRINT_CHARBUFFER1 = 0x01
FINGERPRINT_CHARBUFFER2 = 0x02
MODEL_PATH = "/home/live_finger/newtry27jan/model/may2_4.pth"
DATABASE_PATH = "/home/live_finger/newtry27jan/fingerprints_optical.db"
save_dir = os.path.expanduser("/home/live_finger/newtry27jan/Fingerprints")

# Serial communication constants
BAUD_RATE = 115200
CMD_GENIMG = b'\xEF\x01\xFF\xFF\xFF\xFF\x01\x00\x03\x01\x00\x05'  # Capture Fingerprint
CMD_UPIMAGE = b'\xEF\x01\xFF\xFF\xFF\xFF\x01\x00\x03\x0A\x00\x0E'  # Download Image

class FingerprintSensor:
    def __init__(self, port='/dev/ttyUSB1', baudrate=115200):
        try:
            self.fingerprint = PyFingerprint(port, baudrate, 0xFFFFFFFF, 0x00000000)
            if not self.fingerprint.verifyPassword():
                raise ValueError("The given fingerprint sensor password is wrong!")
            print("Fingerprint sensor initialized successfully.")
            self.is_anti_spoof_enabled = False
            self.last_match_position = None
            
            # Force database schema update
            conn = sqlite3.connect(DATABASE_PATH)
            conn.execute("DROP TABLE IF EXISTS fingerprints")
            conn.commit()
            conn.close()
            
            self.initialize_database()
            # Initialize serial connection
            self.ser = serial.Serial(port, baudrate=BAUD_RATE, timeout=1)
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()

            # Initialize spoof detection model
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            self.model = self.load_model()
            self.transform = transforms.Compose([
                transforms.Resize((224, 224)),
                transforms.ToTensor(),
                transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
            ])
        except Exception as e:
            print(f"Failed to initialize fingerprint sensor: {e}")
            raise e

    def __del__(self):
        """Cleanup when object is destroyed."""
        try:
            if hasattr(self, 'ser') and self.ser.is_open:
                self.ser.close()
            if hasattr(self, 'fingerprint'):
                del self.fingerprint
        except Exception as e:
            print(f"Error during cleanup: {e}")

    def read_data(self):
        """Read data from the sensor"""
        try:
            if self.fingerprint.readImage():
                return "Finger detected"
            return None
        except Exception as e:
            print(f"Error reading data: {e}")
            return None

    def cleanup(self):
        """Clean up resources"""
        try:
            if hasattr(self, 'ser') and self.ser.is_open:
                self.ser.close()
            if hasattr(self, 'fingerprint'):
                del self.fingerprint
        except Exception as e:
            print(f"Error during cleanup: {e}")

    def initialize_database(self):
        try:
            # Create database directory if it doesn't exist
            db_dir = os.path.dirname(DATABASE_PATH)
            if not os.path.exists(db_dir):
                os.makedirs(db_dir, exist_ok=True)
                print(f"Created database directory: {db_dir}")

            # Create database file if it doesn't exist
            if not os.path.exists(DATABASE_PATH):
                conn = sqlite3.connect(DATABASE_PATH)
                conn.close()
                print(f"Created new database file: {DATABASE_PATH}")

            # Initialize database schema
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
            print(f"Database initialized successfully at: {DATABASE_PATH}")
        except sqlite3.Error as e:
            print(f"Database Initialization Failed: {e}")
            raise e

    def send_command(self, cmd):
        """Send a command to the R307 sensor and read the response."""
        try:
            self.ser.write(cmd)
            response = self.ser.read(12)  # Read standard response
            return response if response else None
        except serial.SerialException as e:
            print(f"Serial communication error: {e}")
            return None

    def read_image_data(self):
        """Optimized image transfer at 115200 baud."""
        image_data = bytearray()
        total_bytes = 36864  # 256 × 288 / 2
        bytes_received = 0

        response = self.send_command(CMD_UPIMAGE)
        if not response or response[9] != 0x00:
            print("⚠️ Failed to request image upload.")
            return None

        start_time = time.time()

        try:
            while bytes_received < total_bytes:
                header = self.ser.read(9)  # Read packet header
                if len(header) != 9:
                    continue  # Skip if incomplete header

                packet_type = header[6]  # Data packet identifier
                data_length = struct.unpack(">H", header[7:9])[0] - 2  # Extract data length
                data = self.ser.read(data_length)  # Read actual fingerprint image data
                self.ser.read(2)  # Read checksum (not needed)

                if len(data) != data_length:
                    continue  # Skip if incomplete

                image_data.extend(data)
                bytes_received += len(data)

                if packet_type == 0x08:  # End of Data Packet
                    break

            elapsed_time = time.time() - start_time
            print(f"⏳ Image read in {elapsed_time:.4f} seconds (Optimized at 115200 baud)")
            return image_data
        except serial.SerialException as e:
            print(f"Error reading image data: {e}")
            return None

    def save_bmp(self, image_data, image_path):
        """Save fingerprint image as BMP."""
        try:
            width, height = 256, 288
            decoded_image = bytearray()

            for byte in image_data:
                high_pixel = (byte & 0xF0)
                low_pixel = (byte & 0x0F) << 4
                decoded_image.extend([high_pixel, low_pixel])

            file_size = 54 + 1024 + len(decoded_image)

            bmp_header = b'BM' + struct.pack('<I', file_size) + b'\x00\x00\x00\x00' + struct.pack('<I', 54 + 1024)
            dib_header = struct.pack('<I', 40) + struct.pack('<i', width) + struct.pack('<i', -height)
            dib_header += struct.pack('<H', 1) + struct.pack('<H', 8) + struct.pack('<I', 0)
            dib_header += struct.pack('<I', len(decoded_image)) + struct.pack('<i', 0) + struct.pack('<i', 0)
            dib_header += struct.pack('<I', 256) + struct.pack('<I', 0)

            palette = b''.join(struct.pack('BBBB', i, i, i, 0) for i in range(256))

            with open(image_path, "wb") as f:
                f.write(bmp_header + dib_header + palette + decoded_image)

            print(f"📸 Image saved as '{image_path}'.")
            return True
        except Exception as e:
            print(f"Error saving BMP: {e}")
            return False

    def capture_and_download(self, image_path):
        """Capture and download fingerprint image using serial communication."""
        try:
            total_start = time.time()

            print("👉 Place your finger on the sensor...")
            response = self.send_command(CMD_GENIMG)
            if not response or response[9] != 0x00:
                print("❌ Fingerprint capture failed.")
                return False

            print("✅ Fingerprint captured!")
            image_data = self.read_image_data()
            if not image_data:
                print("⚠️ Image download failed.")
                return False

            if not self.save_bmp(image_data, image_path):
                return False

            total_elapsed = time.time() - total_start
            print(f"⏳ Total execution time: {total_elapsed:.4f} seconds")
            return True
        except Exception as e:
            print(f"Error in capture_and_download: {e}")
            return False

    def enroll_finger(self, name, update_ui_callback=None, enroll_complete_callback=None):
        """Enroll a new fingerprint."""
        try:
            if update_ui_callback:
                update_ui_callback("🔄 Starting fingerprint enrollment...")

            enroll_id = time.strftime("%Y%m%d_%H%M%S")
            enroll_folder = os.path.join(save_dir, f"enroll_{enroll_id}")
            if not os.path.exists(enroll_folder):
                os.makedirs(enroll_folder)

            # Step 1: Capture first image (GenImg)
            if update_ui_callback:
                update_ui_callback("🔄 Step 1/3: Place finger on sensor for first scan...")

            first_scan_complete = False
            while not first_scan_complete:
                response = self.send_command(CMD_GENIMG)
                if response and response[9] == 0x00:
                    # Get and save first scan image
                    image_data = self.read_image_data()
                    if image_data:
                        image_path1 = os.path.join(enroll_folder, f"scan_1_{enroll_id}.bmp")
                        if self.save_bmp(image_data, image_path1):
                            if enroll_complete_callback:
                                enroll_complete_callback(image_path1)  # Show first scan immediately
                            first_scan_complete = True
                            if update_ui_callback:
                                update_ui_callback("✅ First scan completed successfully.")
                        else:
                            if update_ui_callback:
                                update_ui_callback("❌ Failed to save image, please try again...")
                    else:
                        if update_ui_callback:
                            update_ui_callback("❌ Failed to read image data, please try again...")
                else:
                    if update_ui_callback:
                        update_ui_callback("⚠️ No finger detected, please try again...")
                    time.sleep(0.1)  # Shorter delay for more responsive UI

            # Convert to template
            self.fingerprint.convertImage(FINGERPRINT_CHARBUFFER1)

            if update_ui_callback:
                update_ui_callback("🔄 Step 2/3: Remove finger, then place it again for second scan...")
            time.sleep(1)  # Give time to remove finger

            # Step 2: Capture second image
            second_scan_complete = False
            while not second_scan_complete:
                response = self.send_command(CMD_GENIMG)
                if response and response[9] == 0x00:
                    # Get and save second scan image
                    image_data = self.read_image_data()
                    if image_data:
                        image_path2 = os.path.join(enroll_folder, f"scan_2_{enroll_id}.bmp")
                        if self.save_bmp(image_data, image_path2):
                            if enroll_complete_callback:
                                enroll_complete_callback(image_path2)  # Show second scan immediately
                            second_scan_complete = True
                            if update_ui_callback:
                                update_ui_callback("✅ Second scan completed successfully.")
                        else:
                            if update_ui_callback:
                                update_ui_callback("❌ Failed to save image, please try again...")
                    else:
                        if update_ui_callback:
                            update_ui_callback("❌ Failed to read image data, please try again...")
                else:
                    if update_ui_callback:
                        update_ui_callback("⚠️ No finger detected, please try again...")
                    time.sleep(0.1)  # Shorter delay for more responsive UI

            # Convert and compare
            self.fingerprint.convertImage(FINGERPRINT_CHARBUFFER2)

            if self.fingerprint.compareCharacteristics() == 0:
                if update_ui_callback:
                    update_ui_callback("❌ Fingers do not match. Please try again.")
                raise Exception("Fingers do not match. Please try again.")

            if update_ui_callback:
                update_ui_callback("🔄 Step 3/3: Processing fingerprint data...")

            # Create and store template
            self.fingerprint.createTemplate()
            position_number = self.fingerprint.storeTemplate()

            # Save to database
            db = sqlite3.connect(DATABASE_PATH)
            cursor = db.cursor()
            cursor.execute('INSERT INTO fingerprints (name, template_position) VALUES (?, ?)', (name, position_number))
            db.commit()
            db.close()

            if update_ui_callback:
                update_ui_callback(f"✅ Fingerprint enrolled successfully as {name}.")

            # Return both image paths for final display
            if enroll_complete_callback:
                enroll_complete_callback([image_path1, image_path2])

        except Exception as e:
            if update_ui_callback:
                update_ui_callback(f"❌ Enrollment Failed: {e}")
            raise e

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

            if self.fingerprint.deleteTemplate(template_position):
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
            print(f"Failed to fetch enrolled fingerprints: {e}")
            raise e
        finally:
            db.close()

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

                try:
                    timeout = time.time() + 10
                    while not self.fingerprint.readImage():
                        if time.time() > timeout:
                            update_ui_callback("❌ Timeout: No finger detected.")
                            return
                        time.sleep(0.1)

                    if update_ui_callback:
                        update_ui_callback("✅ Finger detected, processing...")

                    timestamp = time.strftime("%Y%m%d%H%M%S")
                    image_path = os.path.join(save_dir, f"fingerprint_{timestamp}.bmp")
                    if not self.capture_and_download(image_path):
                        raise Exception("Failed to capture and download fingerprint image.")

                    self.fingerprint.convertImage(FINGERPRINT_CHARBUFFER1)
                    result = self.fingerprint.searchTemplate()
                    position_number = result[0]
                    self.last_match_position = position_number
                    is_match = position_number >= 0

                    # Get the name of the matched fingerprint
                    matched_name = None
                    if is_match:
                        try:
                            db = sqlite3.connect(DATABASE_PATH)
                            cursor = db.cursor()
                            cursor.execute('SELECT name FROM fingerprints WHERE template_position = ?', (position_number,))
                            result = cursor.fetchone()
                            if result:
                                matched_name = result[0]
                                if update_ui_callback:
                                    update_ui_callback(f"✅ Fingerprint matched with: {matched_name}")
                            db.close()
                        except Exception as e:
                            if update_ui_callback:
                                update_ui_callback(f"❌ Database error: {str(e)}")

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
                        search_complete_callback(is_match, image_path, spoof_status, matched_name)

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

            finally:
                total_search_time = time.time() - search_start_time
                if update_ui_callback:
                    update_ui_callback(f"⏱️ Total operation time: {total_search_time:.2f} seconds")

        threading.Thread(target=run_search).start()

    def load_model(self):
        """Load the pre-trained spoof detection model."""
        try:
            model = models.resnet50(pretrained=False)
            model.fc = torch.nn.Linear(model.fc.in_features, 2)
            model = model.to(self.device)

            state_dict = torch.load(MODEL_PATH, map_location=self.device)
            if any(k.startswith('module.') for k in state_dict.keys()):
                new_state_dict = OrderedDict()
                for k, v in state_dict.items():
                    name = k[7:] if k.startswith('module.') else k
                    new_state_dict[name] = v
                state_dict = new_state_dict
            model.load_state_dict(state_dict)
            model.eval()
            return model
        except Exception as e:
            print(f"Failed to load spoof detection model: {e}")
            return None

    def spoof_detection_algorithm(self, image_path):
        """Check if the fingerprint is LIVE or FAKE."""
        try:
            if not self.model:
                return "Model not loaded"

            # Load and preprocess image
            image = Image.open(image_path).convert("RGB")
            image_tensor = self.transform(image).unsqueeze(0).to(self.device)

            # Make prediction
            with torch.no_grad():
                outputs = self.model(image_tensor)
                _, preds = torch.max(outputs, 1)
                result = "FAKE" if preds[0] == 1 else "LIVE"

            return result
        except Exception as e:
            print(f"Error in spoof detection: {e}")
            return "Error"

    def toggle_anti_spoof(self):
        """Toggle spoof detection on/off."""
        self.is_anti_spoof_enabled = not self.is_anti_spoof_enabled

# Example usage
if __name__ == "__main__":
    sensor = FingerprintSensor()
    sensor.enroll_finger("test_user", lambda x: print(x), lambda x: print(f"Enroll complete: {x}"))
