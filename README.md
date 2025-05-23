# Fingerprint Authentication System

A robust fingerprint authentication system that supports both capacitive and optical fingerprint sensors with anti-spoofing capabilities.

## Features

- Dual sensor support (Capacitive and Optical)
- Real-time fingerprint enrollment and verification
- Anti-spoofing detection using EfficientNet model
- Separate databases for each sensor type
- Modern and intuitive user interface
- Real-time status updates and feedback

## Requirements

- Python 3.8+
- PyQt6
- PyFingerprint
- PyTorch
- torchvision
- PIL (Pillow)
- pyserial
## Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/fingerprint-system.git
cd fingerprint-system
```

2. Install required packages:
```bash
pip install -r requirements.txt
```

3. Download the model file:
- Place `model.pth` in the `model` directory

## Usage

1. Connect your fingerprint sensor (either capacitive or optical)
2. Run the application:
```bash
python main.py
```

3. Use the interface to:
   - Enroll new fingerprints
   - Search for matches
   - Delete enrolled fingerprints
   - Toggle between sensor types
   - Enable/disable anti-spoofing

## Project Structure

```
fingerprint-system/
├── main.py                 # Application entry point
├── main_window.py         # Main window implementation
├── mainwindow_ui.py       # UI layout definition
├── OptSensor.py          # Optical sensor implementation
├── CapSensor.py          # Capacitive sensor implementation
├── model/                 # Model directory
│   └── model.pth
├── requirements.txt       # Python dependencies
└── README.md             # This file
```

## Database Structure

The system maintains separate databases for each sensor type:
- Capacitive sensor: `fingerprints_capacitive.db`
- Optical sensor: `fingerprints_optical.db`

Each database contains:
- Fingerprint ID
- User name
- Template position

## Acknowledgments

- PyFingerprint library for sensor communication
- PyQt6 for the GUI framework
- PyTorch for the anti-spoofing model 
