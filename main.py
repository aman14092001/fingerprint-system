import sys
from PyQt6.QtWidgets import QApplication
from main_window import MainWindow
import os
import serial

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())