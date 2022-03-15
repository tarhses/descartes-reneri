#!/bin/bash

# This script must be used in a base directory that contains Reneri

# Uninstall SDKMAN!
rm -rf ~/.sdkman

# Uninstall Python dependencies
cd descartes-reneri/scripts
pip3 uninstall -r requirements.txt -y
