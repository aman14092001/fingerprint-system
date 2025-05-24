from PyQt6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                            QPushButton, QLabel, QStatusBar, QTextEdit, QMessageBox,
                            QInputDialog, QDialog, QVBoxLayout, QListWidget, QListWidgetItem,
                            QLineEdit, QGridLayout)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject, QCoreApplication, QTimer
from PyQt6.QtGui import QPixmap, QImage
from mainwindow_ui import Ui_FingerprintApp
from CapSensor import AnotherSensor
from OptSensor import FingerprintSensor
import os
import time
import sqlite3
import sys
from io import StringIO

class SensorSignals(QObject):
    """Signals for sensor communication"""
    update_ui = pyqtSignal(str)
    update_image = pyqtSignal(str)  # For single image
    update_match_status = pyqtSignal(str)
    update_spoof_status = pyqtSignal(str)
    enrollment_error = pyqtSignal(str)  # New signal for enrollment errors
    enrollment_complete = pyqtSignal(list)  # For final image paths
    search_complete = pyqtSignal(bool, str, str, str)  # match status, image path, spoof status, matched name

class SensorThread(QThread):
    """Thread for handling sensor communication"""
    def __init__(self, sensor):
        super().__init__()
        self.sensor = sensor
        self.running = True
        self.signals = SensorSignals()
        
    def run(self):
        while self.running:
            data = self.sensor.read_data()
            if data:
                self.signals.update_ui.emit(data)
                
    def stop(self):
        self.running = False

class KeyboardDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Enter Name")
        self.setModal(True)
        self.name = ""
        
        # Set window flags to ensure proper closing
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint)
        
        # Set window style
        self.setStyleSheet("""
            QDialog {
                background-color: #F5F5F5;
                border-radius: 10px;
            }
            QLineEdit {
                font-size: 24px;
                padding: 10px;
                border: 2px solid #2196F3;
                border-radius: 8px;
                background: white;
                color: #333333;
                font-family: 'Roboto', sans-serif;
            }
            QPushButton {
                font-size: 18px;
                padding: 10px;
                border: none;
                border-radius: 8px;
                background: #2196F3;
                color: white;
                font-weight: bold;
                font-family: 'Roboto', sans-serif;
            }
            QPushButton:hover {
                background: #1976D2;
            }
            QPushButton:pressed {
                background: #0D47A1;
            }
            QPushButton[special="true"] {
                background: #4CAF50;
            }
            QPushButton[special="true"]:hover {
                background: #388E3C;
            }
            QPushButton[special="true"]:pressed {
                background: #1B5E20;
            }
            QPushButton[delete="true"] {
                background: #F44336;
            }
            QPushButton[delete="true"]:hover {
                background: #D32F2F;
            }
            QPushButton[delete="true"]:pressed {
                background: #B71C1C;
            }
        """)
        
        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # Text input with larger font
        self.text_input = QLineEdit()
        self.text_input.setReadOnly(True)
        self.text_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text_input.setMinimumHeight(60)
        layout.addWidget(self.text_input)
        
        # Keyboard layout with modern styling
        keyboard_layout = QGridLayout()
        keyboard_layout.setSpacing(5)
        
        # Define keyboard rows with emoji indicators
        rows = [
            ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0'],
            ['q', 'w', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p'],
            ['a', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l'],
            ['z', 'x', 'c', 'v', 'b', 'n', 'm', '⌫']
        ]
        
        # Create and style keyboard buttons
        for row_idx, row in enumerate(rows):
            for col_idx, key in enumerate(row):
                btn = QPushButton(key)
                btn.setMinimumSize(50, 50)
                
                # Special styling for different button types
                if key == '⌫':
                    btn.setProperty("delete", "true")
                
                btn.clicked.connect(lambda checked, b=key: self.on_key_pressed(b))
                keyboard_layout.addWidget(btn, row_idx, col_idx)
        
        # Add Space and Enter buttons in a separate row
        bottom_layout = QHBoxLayout()
        bottom_layout.setSpacing(5)
        
        space_btn = QPushButton("Space")
        space_btn.setProperty("special", "true")
        space_btn.setMinimumHeight(50)
        space_btn.clicked.connect(lambda: self.on_key_pressed("Space"))
        
        enter_btn = QPushButton("Enter")
        enter_btn.setProperty("special", "true")
        enter_btn.setMinimumHeight(50)
        enter_btn.clicked.connect(lambda: self.on_key_pressed("Enter"))
        
        bottom_layout.addWidget(space_btn, 3)  # 3 parts for space
        bottom_layout.addWidget(enter_btn, 1)  # 1 part for enter
        
        layout.addLayout(keyboard_layout)
        layout.addLayout(bottom_layout)
        self.setLayout(layout)
        
        # Set minimum size for the dialog
        self.setMinimumSize(600, 400)
        
    def on_key_pressed(self, key):
        if key == '⌫':
            self.text_input.setText(self.text_input.text()[:-1])
        elif key == 'Space':
            self.text_input.setText(self.text_input.text() + ' ')
        elif key == 'Enter':
            self.name = self.text_input.text().strip()
            if self.name:  # Only accept if name is not empty
                self.done(QDialog.DialogCode.Accepted)
                return True
            return False
        else:
            self.text_input.setText(self.text_input.text() + key)
        return False

    def closeEvent(self, event):
        """Override close event to ensure proper cleanup"""
        self.done(QDialog.DialogCode.Rejected)
        event.accept()

class EnrollmentThread(QThread):
    """Dedicated thread for enrollment process"""
    def __init__(self, sensor, name):
        super().__init__()
        self.sensor = sensor
        self.name = name
        self.signals = SensorSignals()

    def run(self):
        try:
            def update_ui(message):
                self.signals.update_ui.emit(message)
                
            def on_scan_complete(image_path):
                if isinstance(image_path, list):
                    # This is the final callback with both images
                    self.signals.enrollment_complete.emit(image_path)
                else:
                    # This is a single scan image
                    self.signals.update_image.emit(image_path)
                
            self.sensor.enroll_finger(self.name, 
                                    update_ui_callback=update_ui,
                                    enroll_complete_callback=on_scan_complete)
        except Exception as e:
            self.signals.enrollment_error.emit(str(e))

class SearchThread(QThread):
    """Dedicated thread for search process"""
    def __init__(self, sensor):
        super().__init__()
        self.sensor = sensor
        self.signals = SensorSignals()

    def run(self):
        try:
            def update_ui(message):
                self.signals.update_ui.emit(message)
                
            def on_search_complete(is_match, image_path, spoof_status, matched_name=None):
                self.signals.search_complete.emit(is_match, image_path, spoof_status, matched_name)
                
            self.sensor.search_finger(update_ui_callback=update_ui, 
                                    search_complete_callback=on_search_complete)
        except Exception as e:
            self.signals.update_ui.emit(f"Search error: {str(e)}")

class MainWindow(QMainWindow, Ui_FingerprintApp):
    def __init__(self):
        super().__init__()
        self.setupUi(self)
        
        # Initialize sensor
        try:
            # Start with capacitive sensor by default
            self.current_sensor_type = "Capacitive"
            self.sensor = AnotherSensor()  # Default to capacitive sensor
            self.is_anti_spoof_enabled = False
            self.initialize_database()
            self.current_enrollment_images = []
            self.enrollment_in_progress = False
            
            # Initialize threads as None
            self.enrollment_thread = None
            self.search_thread = None
            
            # Force UI updates
            self.resultsDisplay.setUpdatesEnabled(True)
            self.imageLabel.setUpdatesEnabled(True)
            
            # Message queue and display
            self.message_queue = []
            self.current_messages = []  # List to store current messages
            self.max_messages = 10  # Maximum number of messages to display
            
            # Redirect stdout to capture prints
            self.old_stdout = sys.stdout
            self.stdout_capture = StringIO()
            sys.stdout = self.stdout_capture
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to initialize fingerprint sensor: {str(e)}")
            raise e
        
        # Initialize sensor thread
        self.sensor_thread = SensorThread(self.sensor)
        self.sensor_thread.signals.update_ui.connect(self.append_to_results)
        self.sensor_thread.signals.update_image.connect(self.display_fingerprint_image)
        self.sensor_thread.signals.update_match_status.connect(self.update_match_status)
        self.sensor_thread.signals.update_spoof_status.connect(self.update_spoof_status)
        self.sensor_thread.signals.enrollment_complete.connect(self.on_enrollment_complete)
        self.sensor_thread.signals.search_complete.connect(self.on_search_complete)
        
        # Connect UI signals to slots
        self.enrollButton.clicked.connect(self.open_enroll_dialog)
        self.deleteButton.clicked.connect(self.open_delete_dialog)
        self.searchButton.clicked.connect(self.search_fingerprint)
        self.sensorTypeButton.clicked.connect(self.toggle_sensor_type)
        self.spoofToggleButton.clicked.connect(self.toggle_anti_spoof)
        self.exitButton.clicked.connect(self.close)
        
        # Create directories for storing images
        os.makedirs("fingerprint_images/enroll", exist_ok=True)
        os.makedirs("fingerprint_images/search", exist_ok=True)

    def get_database_path(self):
        """Get the appropriate database path based on current sensor type"""
        if self.current_sensor_type == "Optical":
            return "/home/live_finger/newtry27jan/fingerprints_optical.db"
        else:
            return "/home/live_finger/newtry27jan/fingerprints_capacitive.db"

    def initialize_database(self):
        """Ensure the database exists before using it."""
        try:
            db_path = self.get_database_path()
            
            # Create database directory if it doesn't exist
            db_dir = os.path.dirname(db_path)
            if not os.path.exists(db_dir):
                os.makedirs(db_dir, exist_ok=True)
                print(f"Created database directory: {db_dir}")

            # Create database file if it doesn't exist
            if not os.path.exists(db_path):
                conn = sqlite3.connect(db_path)
                conn.close()
                print(f"Created new database file: {db_path}")

            # Initialize database schema
            db = sqlite3.connect(db_path)
            cursor = db.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS fingerprints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    template_position INTEGER NOT NULL UNIQUE
                )
            ''')
            db.commit()
            db.close()
            print(f"Database initialized successfully at: {db_path}")
        except Exception as e:
            QMessageBox.critical(self, "Database Error", f"Database Initialization Failed: {str(e)}")
            raise e

    def append_to_results(self, message):
        """Add message to queue and display it"""
        # Add message to queue
        self.message_queue.append(message)
        
        # Update display
        self.update_messages_display()

    def update_messages_display(self):
        """Update the messages display with all current messages"""
        # Get terminal output
        terminal_output = self.stdout_capture.getvalue()
        self.stdout_capture.truncate(0)
        self.stdout_capture.seek(0)
        
        # Combine terminal output with message queue
        all_messages = []
        if terminal_output.strip():
            all_messages.extend(terminal_output.strip().split('\n'))
        all_messages.extend(self.message_queue)
        
        # Icon mapping for commercial-style messages (priority order)
        icon_mapping = {
            "Starting": "", "Step": "", "Scan": "", 
            "Template": "", "success": "", "failed": "",
            "detected": "", "search": "", "error": "",
            "Waiting": "", "Match": "", "No match": "",
            "Switched": "", "Anti-spoof": "",
            "Deleting": "", "Enabling": "", "Disabling": "",
            "Enrollment": "", "Processing": "", "Initializing": "",
            "Please": "", "Finger": "", "Place": "",
            "Remove": "", "Press": "", "Try": "",
            "Total operation time": ""
        }
        
        # Filter and format messages
        filtered_messages = []
        for msg in all_messages:
            # Skip unwanted messages
            if any(unwanted in msg for unwanted in [
                "Fingerprint matched, but name not found in database",
                "Fingerprint matched! ID:",
                "Performing spoof detection...",
                "Spoof detection result:",
                "The recommended registration number is:",
                "Data written to",
                "Image saved as",
                "The fingerprint is saved successfully, and the id is:",
                "Successful fingerprint match found!",
                "The matching fingerprint ID is:"
            ]):
                continue
                
            # Remove all existing emojis from the message
            for icon in icon_mapping.values():
                msg = msg.replace(icon, '').strip()
            
            # Add the most appropriate single emoji
            formatted = False
            for key, icon in icon_mapping.items():
                if key.lower() in msg.lower():
                    filtered_messages.append(f"{icon} {msg}")
                    formatted = True
                    break
            if not formatted:
                filtered_messages.append(msg)
        
        # Keep only the most recent messages
        if len(filtered_messages) > self.max_messages:
            filtered_messages = filtered_messages[-self.max_messages:]
        
        # Update display
        self.resultsDisplay.clear()
        self.resultsDisplay.setText("System Status:\n" + '\n'.join(filtered_messages))
        
        # Scroll to bottom
        self.resultsDisplay.verticalScrollBar().setValue(
            self.resultsDisplay.verticalScrollBar().maximum()
        )

    def clear_current_message(self):
        """Clear the current message and show the next one if available"""
        if self.message_queue:
            self.message_queue.pop(0)
            self.update_messages_display()

    def open_enroll_dialog(self):
        """Open dialog for enrolling a new fingerprint"""
        if self.enrollment_in_progress:
            self.append_to_results("⚠️ Enrollment already in progress")
            return
            
        self.enrollment_in_progress = True
        self.enrollButton.setEnabled(False)  # Disable button during enrollment
        
        keyboard_dialog = KeyboardDialog(self)
        result = keyboard_dialog.exec()
        
        if result == QDialog.DialogCode.Accepted:
            name = keyboard_dialog.name
            if not name:
                self.append_to_results("❌ Enrollment cancelled - no name provided")
                self.enrollment_in_progress = False
                self.enrollButton.setEnabled(True)
                return
            
            # Clear any existing messages
            self.resultsDisplay.clear()
            self.message_queue.clear()
            self.current_messages.clear()
            
            self.append_to_results(f"📝 Starting enrollment for {name}...")
            self.append_to_results("🔄 Please follow on-screen instructions...")
            
            # Create and start enrollment thread
            self.enrollment_thread = EnrollmentThread(self.sensor, name)
            self.enrollment_thread.signals.update_ui.connect(self.append_to_results)
            self.enrollment_thread.signals.update_image.connect(self.display_fingerprint_image)
            self.enrollment_thread.signals.enrollment_complete.connect(self.on_enrollment_complete)
            self.enrollment_thread.signals.enrollment_error.connect(self.handle_enrollment_error)
            self.enrollment_thread.finished.connect(self.on_enrollment_thread_finished)
            self.enrollment_thread.start()
        else:
            self.enrollment_in_progress = False
            self.enrollButton.setEnabled(True)

    def handle_enrollment_error(self, error_message):
        """Handle enrollment errors"""
        self.append_to_results(f"❌ Enrollment Failed: {error_message}")
        QMessageBox.critical(self, "Enrollment Error", 
            f"Enrollment failed due to:\n{error_message}\n\nPlease check sensor connection and try again.")

    def display_fingerprint_image(self, image_path):
        """Display the fingerprint image and force update"""
        if not image_path or not os.path.exists(image_path):
            self.imageLabel.setText("No image available")
            return

        try:
            pixmap = QPixmap(image_path)
            if pixmap.isNull():
                self.imageLabel.setText("Failed to load image")
                return

            scaled_pixmap = pixmap.scaled(
                self.imageLabel.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            self.imageLabel.setPixmap(scaled_pixmap)
            self.imageLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.imageLabel.repaint()
            QCoreApplication.processEvents()
        except Exception as e:
            self.imageLabel.setText(f"Error: {str(e)}")
        
    def open_delete_dialog(self):
        """Open dialog for deleting a fingerprint"""
        self.on_delete()

    def confirm_delete(self, fingerprint_id):
        """Confirm and delete a fingerprint"""
        try:
            # Get fingerprint details from database
            db_path = self.get_database_path()
            db = sqlite3.connect(db_path)
            cursor = db.cursor()
            cursor.execute("SELECT name FROM fingerprints WHERE id = ?", (fingerprint_id,))
            result = cursor.fetchone()
            
            if not result:
                self.append_to_results("❌ Delete failed: Fingerprint not found")
                QMessageBox.warning(self, "Error", "Fingerprint not found in database.")
                return
            
            name = result[0]
            
            # Confirm deletion
            reply = QMessageBox.question(
                self,
                "Confirm Deletion",
                f"Delete fingerprint for:\n{name} (ID: {fingerprint_id})?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            
            if reply == QMessageBox.StandardButton.Yes:
                self.append_to_results(f"🗑️ Deleting {name}'s fingerprint...")
                # Delete from sensor
                if self.sensor.delete_finger(fingerprint_id):
                    # Delete from database
                    cursor.execute("DELETE FROM fingerprints WHERE id = ?", (fingerprint_id,))
                    db.commit()
                    self.append_to_results(f"✅ Successfully deleted {name}'s fingerprint")
                else:
                    self.append_to_results("❌ Failed to delete from sensor")
                    QMessageBox.warning(self, "Error", "Sensor deletion failed")
            else:
                self.append_to_results("⚠️ Delete operation cancelled")
            
            db.close()
            
        except Exception as e:
            self.append_to_results(f"❌ Delete error: {str(e)}")
            QMessageBox.critical(self, "Error", f"Deletion failed:\n{str(e)}")
            if 'db' in locals():
                db.close()

    def on_delete(self):
        """Handle delete button click"""
        try:
            # Get all fingerprints from database
            db_path = self.get_database_path()
            db = sqlite3.connect(db_path)
            cursor = db.cursor()
            cursor.execute("SELECT id, name FROM fingerprints")
            fingerprints = cursor.fetchall()
            db.close()
            
            if not fingerprints:
                QMessageBox.information(self, "No Fingerprints", "No fingerprints found in the database.")
                return
            
            # Create selection dialog
            dialog = QDialog(self)
            dialog.setWindowTitle("Select Fingerprint to Delete")
            layout = QVBoxLayout()
            
            list_widget = QListWidget()
            for id, name in fingerprints:
                item = QListWidgetItem(f"{name} (ID: {id})")
                item.setData(Qt.ItemDataRole.UserRole, id)
                list_widget.addItem(item)
            
            layout.addWidget(list_widget)
            
            # Add buttons
            button_layout = QHBoxLayout()
            delete_button = QPushButton("Delete")
            cancel_button = QPushButton("Cancel")
            
            def on_delete_clicked():
                selected_items = list_widget.selectedItems()
                if selected_items:
                    fingerprint_id = selected_items[0].data(Qt.ItemDataRole.UserRole)
                    dialog.accept()
                    self.confirm_delete(fingerprint_id)
            
            delete_button.clicked.connect(on_delete_clicked)
            cancel_button.clicked.connect(dialog.reject)
            
            button_layout.addWidget(delete_button)
            button_layout.addWidget(cancel_button)
            layout.addLayout(button_layout)
            
            dialog.setLayout(layout)
            dialog.exec()
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to open delete dialog: {str(e)}")

    def search_fingerprint(self):
        """Search for a fingerprint match"""
        self.resultsDisplay.clear()
        self.message_queue.clear()
        self.current_messages.clear()
        
        self.append_to_results("🔍 Starting fingerprint search...")
        self.update_match_status("Match Status: Initializing...")
        self.update_spoof_status("Spoof Status: Disabled")
        self.append_to_results("🔄 Waiting for finger placement...")
        
        try:
            # Create and start search thread
            self.search_thread = SearchThread(self.sensor)
            self.search_thread.signals.update_ui.connect(self.append_to_results)
            self.search_thread.signals.update_image.connect(self.display_fingerprint_image)
            self.search_thread.signals.search_complete.connect(self.on_search_complete)
            self.search_thread.finished.connect(self.on_search_thread_finished)
            self.search_thread.start()
        except Exception as e:
            self.append_to_results(f"❌ Search initialization failed: {str(e)}")
            QMessageBox.critical(self, "Search Error", 
                f"Failed to start search:\n{str(e)}\n\nCheck sensor connection and try again.")

    def on_search_thread_finished(self):
        """Handle search thread completion"""
        if self.search_thread:
            self.search_thread.deleteLater()
            self.search_thread = None

    def on_search_complete(self, is_match, image_path, spoof_status, matched_name=None):
        """Handle search completion"""
        self.display_fingerprint_image(image_path)
        
        if is_match:
            if matched_name:
                self.update_match_status(f"Match Status: Matched\nName: {matched_name}")
                self.append_to_results(f"✅ Fingerprint matched with: {matched_name}")
            else:
                self.update_match_status("Match Status: Matched")
                self.append_to_results("✅ Fingerprint matched")
        else:
            self.update_match_status("Match Status: No Match")
            self.append_to_results("❌ No match found.")
            
        # Only update spoof status if anti-spoof detection is enabled
        if self.sensor.is_anti_spoof_enabled:
            self.update_spoof_status(f"Spoof Status: {spoof_status}")
        else:
            self.update_spoof_status("Spoof Status: Disabled")

    def update_match_status(self, status):
        """Update match status display"""
        if not status:
            status = "Match Status: Matched or Not Matched"
        self.matchStatusDisplay.setText(status)
        
    def update_spoof_status(self, status):
        """Update spoof status display"""
        if not status:
            status = "Spoof Status: Live or Fake"
        self.spoofStatusDisplay.setText(status)
        
    def toggle_sensor_type(self):
        """Toggle sensor type between Capacitive and Optical"""
        try:
            self.sensorTypeButton.setEnabled(False)
            
            # Clean up current sensor and thread
            if hasattr(self, 'sensor_thread'):
                self.sensor_thread.stop()
                self.sensor_thread.wait()
                self.sensor_thread = None
                
            if hasattr(self, 'sensor'):
                if hasattr(self.sensor, 'ser') and self.sensor.ser.is_open:
                    self.sensor.ser.close()
                if hasattr(self.sensor, 'cleanup'):
                    self.sensor.cleanup()
                del self.sensor
                
            # Switch sensor type with clear feedback
            new_type = "Optical" if self.current_sensor_type == "Capacitive" else "Capacitive"
            self.append_to_results(f"🔄 Switching to {new_type} sensor...")
            
            # Switch sensor type
            if self.current_sensor_type == "Capacitive":
                self.current_sensor_type = "Optical"
                self.sensor = FingerprintSensor(port='/dev/ttyUSB1')
                self.append_to_results("✅ Optical sensor initialized successfully")
            else:
                self.current_sensor_type = "Capacitive"
                self.sensor = AnotherSensor(port='/dev/ttyUSB0')
                self.append_to_results("✅ Capacitive sensor initialized successfully")
                
            # Update UI and restart thread
            self.update_sensor_type_button(self.current_sensor_type)
            QCoreApplication.processEvents()
            
            # Reinitialize sensor thread with new sensor
            self.sensor_thread = SensorThread(self.sensor)
            self.sensor_thread.signals.update_ui.connect(self.append_to_results)
            self.sensor_thread.signals.update_image.connect(self.display_fingerprint_image)
            self.sensor_thread.signals.update_match_status.connect(self.update_match_status)
            self.sensor_thread.signals.update_spoof_status.connect(self.update_spoof_status)
            self.sensor_thread.signals.enrollment_complete.connect(self.on_enrollment_complete)
            self.sensor_thread.signals.search_complete.connect(self.on_search_complete)
            
            # Re-enable the button after a short delay
            QTimer.singleShot(1000, lambda: self.sensorTypeButton.setEnabled(True))
            
        except Exception as e:
            self.append_to_results(f"❌ Sensor switch failed: {str(e)}")
            QMessageBox.critical(self, "Error", f"Failed to switch sensor type: {str(e)}")
            self.update_sensor_type_button(self.current_sensor_type)
            self.sensorTypeButton.setEnabled(True)

    def toggle_anti_spoof(self):
        """Toggle anti-spoof detection"""
        if self.sensor:
            try:
                # Toggle the status
                self.sensor.is_anti_spoof_enabled = not self.sensor.is_anti_spoof_enabled
                status = "Enabled" if self.sensor.is_anti_spoof_enabled else "Disabled"
                
                # Update UI with simple status
                self.append_to_results(f"🛡️ Anti-spoof detection {status}")
                self.update_spoof_status(f"Spoof Status: {status}")
                
                # Force immediate UI update
                self.spoofStatusDisplay.repaint()
                QCoreApplication.processEvents()
                
            except Exception as e:
                self.append_to_results(f"❌ Spoof toggle failed: {str(e)}")
                QMessageBox.critical(self, "Error", f"Failed to toggle spoof detection:\n{str(e)}")
        else:
            self.append_to_results("⚠️ No active sensor for spoof detection")

    def on_enrollment_complete(self, image_paths):
        """Handle enrollment completion"""
        if len(image_paths) > 1:
            self.display_fingerprint_image(image_paths[1])  # Show the second scan
            self.append_to_results("✅ Enrollment completed successfully!")
            
            # Get the name of the last enrolled fingerprint
            try:
                db_path = self.get_database_path()
                db = sqlite3.connect(db_path)
                cursor = db.cursor()
                cursor.execute('SELECT name FROM fingerprints ORDER BY id DESC LIMIT 1')
                result = cursor.fetchone()
                db.close()
                
                if result:
                    enrolled_name = result[0]
                    self.append_to_results(f"✅ Fingerprint enrolled for: {enrolled_name}")
                    self.update_match_status(f"Match Status: Enrolled\nName: {enrolled_name}")
            except Exception as e:
                print(f"Error getting enrolled name: {e}")

    def on_enrollment_thread_finished(self):
        """Handle enrollment thread completion"""
        if self.enrollment_thread:
            self.enrollment_thread.deleteLater()
            self.enrollment_thread = None
        self.enrollment_in_progress = False
        self.enrollButton.setEnabled(True)  # Re-enable button after enrollment

    def closeEvent(self, event):
        """Clean up on window close"""
        try:
            # Restore stdout
            sys.stdout = self.old_stdout
            
            # Stop the sensor thread
            if hasattr(self, 'sensor_thread'):
                self.sensor_thread.stop()
                self.sensor_thread.wait()
            
            # Clean up the sensor
            if hasattr(self, 'sensor'):
                # Close the serial connection if it exists
                if hasattr(self.sensor, 'serial'):
                    self.sensor.serial.close()
                
                # Clean up any other resources
                if hasattr(self.sensor, 'cleanup'):
                    self.sensor.cleanup()
        except Exception as e:
            print(f"Error during cleanup: {str(e)}")
        finally:
            event.accept()

    def delete_fingerprint(self):
        try:
            # Get the selected fingerprint name
            selected_items = self.fingerprintList.selectedItems()
            if not selected_items:
                self.update_messages_display("❌ Please select a fingerprint to delete")
                return

            name = selected_items[0].text()
            
            # Get the template position from the database
            db_path = self.get_database_path()
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT template_position FROM fingerprints WHERE name = ?", (name,))
            result = cursor.fetchone()
            conn.close()
            
            if not result:
                self.update_messages_display(f"❌ No fingerprint found for {name}")
                return
                
            template_position = result[0]
            
            # Delete from sensor
            if self.current_sensor_type == "Optical":
                self.sensor = FingerprintSensor(port='/dev/ttyUSB1')
                self.sensor.delete_finger(template_position)
            else:
                self.sensor = AnotherSensor(port='/dev/ttyUSB0')
                self.sensor.delete_finger(template_position)
                
            # Delete from database
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM fingerprints WHERE template_position = ?", (template_position,))
            conn.commit()
            conn.close()
            
            # Update UI
            self.update_fingerprint_list()
            self.update_messages_display(f"✅ Fingerprint for {name} deleted successfully")
            
        except Exception as e:
            self.update_messages_display(f"❌ Error deleting fingerprint: {str(e)}") 
