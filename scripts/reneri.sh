reneri_full() {
  mvn clean package org.pitest:pitest-maven:mutationCoverage
  local report=`get_last_pit_report`
  cp $report/*.json target
  mvn eu.stamp-project:reneri:observeMethods eu.stamp-project:reneri:observeTests eu.stamp-project:reneri:hints
}

reneri_reports() {
  local script='/home/pierre/Projets/muted/descartes-reneri/scripts'
  local report=`get_last_pit_report`
  cp $report/mutations.* .
  cp -r target/reneri/observations .
  echo '{"project":"","revision":"","folder":""}' > project.json
  $script/venv/bin/python $script/generate_reports.py .
  cat `find observations/methods/*/*/report.md` > report.md
  pandoc report.md -s -o report.html
}

get_last_pit_report() {
  local latest=`ls target/pit-reports | sort | tail -1`
  echo "target/pit-reports/$latest"
}

# time reneri       # 27s
