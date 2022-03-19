#!/bin/bash

# This script must be used in a base directory that contains pom.pit.xml,
# report.pit.sh, and the SUT

# Swap the SUT's pom.xml
cp pom.pit.xml 2048/pom.xml
cp report.pit.sh 2048/report.sh
