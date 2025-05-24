#!/bin/bash

# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Upgrade pip
pip install --upgrade pip

# Install required packages
pip install -r requirements.txt

# Create necessary directories
mkdir -p fingerprint_images/enroll
mkdir -p fingerprint_images/search
mkdir -p model

echo "Setup completed! Please make sure to:"
echo "1. Place the model file 'model.pth' in the 'model' directory"
echo "2. Update the database paths in main_window.py if needed"
echo "3. Update the sensor ports in CapSensor.py and OptSensor.py if needed" 
