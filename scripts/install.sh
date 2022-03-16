#!/bin/bash

# This script must be used in a base directory that contains Reneri and the SUT

# Install SDKMAN! (Java, Maven, and Reneri)
cd descartes-reneri
curl -s https://get.sdkman.io | bash
source ~/.sdkman/bin/sdkman-init.sh
sdk install java 11.0.14-tem
sdk install maven
mvn install

# Install Python dependencies
cd scripts
pip3 install -r requirements.txt

# Open the SUT in VSCode
cd ../../2048
code .
cd ..
