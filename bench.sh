#!/usr/bin/env bash
export BASE_BENCH_DIR=bench/name=${1?}/
mkdir -p "${BASE_BENCH_DIR}"

export BENCH_MAX_ROW_COUNT=1000

for backend in "sqlite" "postgres" "mysql" "mariadb"; do
  for columns in $(seq 0 5 25); do
    echo "backend=$backend" "columns=$columns"
    export BENCH_DIR="${BASE_BENCH_DIR}"/backend=${backend}/
    BENCH_NUM_COLUMNS=$columns ./tests/runtests.py queries.test_bulk_update.BulkUpdateDev --parallel=1 --settings=test_${backend}
  done
done
