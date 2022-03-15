#!/bin/bash

# This script must be used with the SUT as the current working directory

# Generate and open Reneri report
python3 ../descartes-reneri/scripts/generate_report.py
firefox report.html &
