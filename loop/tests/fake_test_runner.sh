#!/usr/bin/env bash
# Stands in for a project's test command. Reads tests.txt in cwd: one word per
# test, PASS or FAIL. Prints pytest-style output the tests scorer parses.
set -u
[ -f tests.txt ] || { echo "1 passed in 0.01s"; exit 0; }
p=0; f=0; i=0
for w in $(cat tests.txt); do
  i=$((i+1))
  if [ "$w" = "FAIL" ]; then f=$((f+1)); echo "FAILED tests/test_fake.py::test_$i - assert"; else p=$((p+1)); fi
done
if [ "$f" -gt 0 ]; then echo "$f failed, $p passed in 0.02s"; exit 1; fi
echo "$p passed in 0.02s"; exit 0
