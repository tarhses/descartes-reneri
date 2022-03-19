#!/bin/bash

# This script must be used with the SUT as the current working directory

# Generate and open PIT report
if mvn test-compile org.pitest:pitest-maven:mutationCoverage
then
  report=$(ls target/pit-reports | sort | tail -1)
  firefox target/pit-reports/$report/index.html
fi
