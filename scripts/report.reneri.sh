#!/bin/bash

# This script must be used with the SUT as the current working directory

# Generate and open Reneri report
if python3 ../descartes-reneri/scripts/generate_report.py
then
  firefox report.html
fi
