from PyQt6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                            QPushButton, QLabel, QStatusBar, QTextEdit, QMessageBox,
                            QInputDialog, QDialog, QVBoxLayout, QListWidget, QListWidgetItem,
                            QLineEdit, QGridLayout)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject, QCoreApplication, QTimer
from PyQt6.QtGui import QPixmap, QImage
from mainwindow_ui import Ui_FingerprintApp
from OptSensor import FingerprintSensor
from CapSensor import AnotherSensor
import os
import time
import sqlite3

class SensorSignals(QObject):
    """Signals for sensor communication"""
    update_ui = pyqtSignal(str)
    update_image = pyqtSignal(str)  # For single image
    update_match_status = pyqtSignal(str)
    update_spoof_status = pyqtSignal(str)
    enrollment_error = pyqtSignal(str)  # New signal for enrollment errors
    enrollment_complete = pyqtSignal(list)  # For final image paths
    search_complete = pyqtSignal(bool, str, str)  # match status, image path, spoof status

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
        
        layout = QVBoxLayout()
        
        # Text input
        self.text_input = QLineEdit()
        self.text_input.setReadOnly(True)
        layout.addWidget(self.text_input)
        
        # Keyboard layout
        keyboard_layout = QGridLayout()
        buttons = [
            '1', '2', '3', '4', '5', '6', '7', '8', '9', '0',
            'q', 'w', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p',
            'a', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l',
            'z', 'x', 'c', 'v', 'b', 'n', 'm', '⌫', 'Space', 'Enter'
        ]
        
        row = 0
        col = 0
        for button in buttons:
            btn = QPushButton(button)
            btn.clicked.connect(lambda checked, b=button: self.on_key_pressed(b))
            keyboard_layout.addWidget(btn, row, col)
            col += 1
            if col > 9 or (row == 1 and col > 9) or (row == 2 and col > 8):
                col = 0
                row += 1
                
        layout.addLayout(keyboard_layout)
        self.setLayout(layout)
        
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
                
            def on_search_complete(is_match, image_path, spoof_status):
                self.signals.search_complete.emit(is_match, image_path, spoof_status)
                
            self.sensor.search_finger(update_ui_callback=update_ui, 
                                    search_complete_callback=on_search_complete)
        except Exception as e:
            self.signals.update_ui.emit(f"Search error: {str(e)}")

class MainWindow(QMainWindow, Ui_FingerprintApp):
    # Update database paths
    CAPACITIVE_DATABASE_PATH = "/home/live_finger/newtry27jan/fingerprints_capacitive.db"
    OPTICAL_DATABASE_PATH = "/home/live_finger/newtry27jan/fingerprints_optical.db"
    
    def __init__(self):
        super().__init__()
        self.setupUi(self)
        
        # Initialize sensor
        try:
            self.sensor = AnotherSensor()  # Start with capacitive sensor
            self.current_sensor_type = "Capacitive"
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
            
            # Timer for message display
            self.message_timer = QTimer()
            self.message_timer.timeout.connect(self.clear_current_message)
            self.message_timer.setSingleShot(True)
            
            # Message queue
            self.message_queue = []
            self.current_message = ""
            
            # Set initial button color for capacitive sensor (purple)
            self.sensorTypeButton.setStyleSheet("""
                QPushButton {
                    padding: 10px;
                    font-size: 18px;
                    background: #9C27B0;
                    color: white;
                    border-radius: 8px;
                    font-weight: bold;
                    font-family: Arial, sans-serif;
                }
                QPushButton:hover {
                    background: #7B1FA2;
                }
                QPushButton:disabled {
                    background: #BA68C8;
                }
            """)
            
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
        
    def initialize_database(self):
        """Ensure the database exists before using it."""
        try:
            # Use the appropriate database path based on current sensor
            db_path = self.CAPACITIVE_DATABASE_PATH if self.current_sensor_type == "Capacitive" else self.OPTICAL_DATABASE_PATH
            
            db_dir = os.path.dirname(db_path)
            if not os.path.exists(db_dir):
                os.makedirs(db_dir, exist_ok=True)

            db = sqlite3.connect(db_path)
            cursor = db.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS fingerprints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    template_position INTEGER
                )
            ''')
            db.commit()
            db.close()
        except Exception as e:
            QMessageBox.critical(self, "Database Error", f"Database Initialization Failed: {str(e)}")
            raise e

    def clear_current_message(self):
        """Clear the current message and show the next one if available"""
        self.resultsDisplay.clear()
        self.current_message = ""
        
        if self.message_queue:
            self.current_message = self.message_queue.pop(0)
            self.resultsDisplay.setText(self.current_message)
            self.message_timer.start(1000)  # Show next message after 1.5 seconds
        else:
            self.resultsDisplay.clear()

    def append_to_results(self, message):
        """Add message to queue and start display if not already running"""
        self.message_queue.append(message)
        
        if not self.message_timer.isActive() and not self.current_message:
            self.clear_current_message()

    def open_enroll_dialog(self):
        """Open dialog for enrolling a new fingerprint"""
        if self.enrollment_in_progress:
            return
            
        self.enrollment_in_progress = True
        self.enrollButton.setEnabled(False)  # Disable button during enrollment
        
        keyboard_dialog = KeyboardDialog(self)
        result = keyboard_dialog.exec()
        
        if result == QDialog.DialogCode.Accepted:
            name = keyboard_dialog.name
            if not name:
                self.enrollment_in_progress = False
                self.enrollButton.setEnabled(True)
                return
            
            # Clear any existing messages
            self.resultsDisplay.clear()
            self.message_queue.clear()
            self.current_message = ""
            
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
        self.append_to_results(f"Enrollment Failed: {error_message}")
        QMessageBox.critical(self, "Enrollment Failed", error_message)

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
            db = sqlite3.connect(self.get_current_database_path())
            cursor = db.cursor()
            cursor.execute("SELECT name FROM fingerprints WHERE id = ?", (fingerprint_id,))
            result = cursor.fetchone()
            
            if not result:
                QMessageBox.warning(self, "Error", "Fingerprint not found in database.")
                return
            
            name = result[0]
            
            # Confirm deletion
            reply = QMessageBox.question(
                self,
                "Confirm Deletion",
                f"Are you sure you want to delete the fingerprint for {name}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            
            if reply == QMessageBox.StandardButton.Yes:
                # Delete from sensor
                if self.sensor.delete_finger(fingerprint_id):
                    # Delete from database
                    cursor.execute("DELETE FROM fingerprints WHERE id = ?", (fingerprint_id,))
                    db.commit()
                    self.append_to_results(f"Fingerprint for {name} deleted successfully.")
                else:
                    QMessageBox.warning(self, "Error", "Failed to delete fingerprint from sensor.")
            
            db.close()
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to delete fingerprint: {str(e)}")
            if 'db' in locals():
                db.close()

    def on_delete(self):
        """Handle delete button click"""
        try:
            # Get all fingerprints from database
            db = sqlite3.connect(self.get_current_database_path())
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
        self.update_match_status("Match Status: Searching...")
        self.update_spoof_status("Spoof Status: Analyzing...")
        
        try:
            # Create and start search thread
            self.search_thread = SearchThread(self.sensor)
            self.search_thread.signals.update_ui.connect(self.append_to_results)
            self.search_thread.signals.update_image.connect(self.display_fingerprint_image)
            self.search_thread.signals.search_complete.connect(self.on_search_complete)
            self.search_thread.finished.connect(self.on_search_thread_finished)
            self.search_thread.start()
        except Exception as e:
            QMessageBox.critical(self, "Search Failed", f"An error occurred: {str(e)}")

    def on_search_thread_finished(self):
        """Handle search thread completion"""
        if self.search_thread:
            self.search_thread.deleteLater()
            self.search_thread = None

    def on_search_complete(self, is_match, image_path, spoof_status):
        """Handle search completion"""
        self.display_fingerprint_image(image_path)
        
        if is_match:
            try:
                db = sqlite3.connect(self.get_current_database_path())
                cursor = db.cursor()
                cursor.execute("SELECT name FROM fingerprints WHERE template_position = ?", 
                             (self.sensor.last_match_position,))
                result = cursor.fetchone()
                db.close()
                
                if result:
                    matched_name = result[0]
                    self.update_match_status("Match Status: Matched")
                    self.append_to_results(f"Fingerprint matched as {matched_name}.")
                else:
                    self.update_match_status("Match Status: Matched (Name not found)")
                    self.append_to_results("Fingerprint matched, but name not found in database.")
            except Exception as e:
                self.append_to_results(f"Database error: {str(e)}")
        else:
            self.update_match_status("Match Status: No Match")
            self.append_to_results("No match found.")
            
        self.update_spoof_status(f"Spoof Status: {spoof_status}")
        
    def update_match_status(self, status):
        """Update match status display"""
        self.matchStatusDisplay.setText(status)
        
    def update_spoof_status(self, status):
        """Update spoof status display"""
        self.spoofStatusDisplay.setText(status)
        
    def toggle_sensor_type(self):
        """Toggle sensor type between Capacitive and Optical"""
        try:
            # Clean up current sensor
            if hasattr(self, 'sensor'):
                self.sensor.cleanup()
            
            # Switch sensor type
            if self.current_sensor_type == "Capacitive":
                self.sensor = FingerprintSensor()  # Switch to optical
                self.current_sensor_type = "Optical"
                self.sensorTypeButton.setText("Sensor Type: Optical")
                # Set button color for optical sensor (blue)
                self.sensorTypeButton.setStyleSheet("""
                    QPushButton {
                        padding: 10px;
                        font-size: 18px;
                        background: #2196F3;
                        color: white;
                        border-radius: 8px;
                        font-weight: bold;
                        font-family: Arial, sans-serif;
                    }
                    QPushButton:hover {
                        background: #1976D2;
                    }
                    QPushButton:disabled {
                        background: #64B5F6;
                    }
                """)
            else:
                self.sensor = AnotherSensor()  # Switch to capacitive
                self.current_sensor_type = "Capacitive"
                self.sensorTypeButton.setText("Sensor Type: Capacitive")
                # Set button color for capacitive sensor (purple)
                self.sensorTypeButton.setStyleSheet("""
                    QPushButton {
                        padding: 10px;
                        font-size: 18px;
                        background: #9C27B0;
                        color: white;
                        border-radius: 8px;
                        font-weight: bold;
                        font-family: Arial, sans-serif;
                    }
                    QPushButton:hover {
                        background: #7B1FA2;
                    }
                    QPushButton:disabled {
                        background: #BA68C8;
                    }
                """)
            
            # Initialize database for new sensor
            self.initialize_database()
            
            # Update UI
            self.resultsDisplay.clear()
            self.imageLabel.clear()
            self.matchStatusDisplay.clear()
            self.spoofStatusDisplay.clear()
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to switch sensor: {str(e)}")

    def toggle_anti_spoof(self):
        """Toggle anti-spoof detection"""
        if self.sensor:
            self.sensor.toggle_anti_spoof()
            status = "Enabled" if self.sensor.is_anti_spoof_enabled else "Disabled"
            self.update_spoof_status(f"Spoof Status: {status}")
        else:
            QMessageBox.critical(self, "Error", "Fingerprint sensor not initialized.")
            
    def on_enrollment_complete(self, image_paths):
        """Handle enrollment completion"""
        if len(image_paths) > 1:
            self.display_fingerprint_image(image_paths[1])  # Show the second scan
            self.append_to_results("Enrollment completed successfully!")

    def on_enrollment_thread_finished(self):
        """Handle enrollment thread completion"""
        if self.enrollment_thread:
            self.enrollment_thread.deleteLater()
            self.enrollment_thread = None
        self.enrollment_in_progress = False
        self.enrollButton.setEnabled(True)  # Re-enable button after enrollment

    def get_current_database_path(self):
        """Get the current database path based on sensor type"""
        return self.CAPACITIVE_DATABASE_PATH if self.current_sensor_type == "Capacitive" else self.OPTICAL_DATABASE_PATH

    def closeEvent(self, event):
        """Clean up on window close"""
        try:
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