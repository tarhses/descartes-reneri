#!/bin/bash

# This script must be used in a base directory that contains pom.reneri.xml,
# report.reneri.sh, and the SUT

# Swap the SUT's pom.xml
cp pom.reneri.xml 2048/pom.xml
cp report.reneri.sh 2048/report.sh
