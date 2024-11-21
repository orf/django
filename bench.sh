#!/usr/bin/env bash
export BENCH_DIR=bench/name=${1?}/
mkdir -p ${BENCH_DIR}

for columns in $(seq 0 10 100); do
  echo "columns=$columns"
  BENCH_NUM_COLUMNS=$columns ./tests/runtests.py queries.test_bulk_update.BulkUpdateDev --parallel=1
done
