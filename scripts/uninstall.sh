#!/bin/bash

# This script must be used in a base directory that contains Reneri

# Uninstall Reneri and SDKMAN!
rm -rf ~/.m2/repository/eu/stamp-project/reneri
rm -rf ~/.sdkman

# Uninstall Python dependencies
cd descartes-reneri/scripts
pip3 uninstall -r requirements.txt -y
cd ../..
