#!/bin/bash

while getopts c:h:p:d:w: flag
do
    case "${flag}" in
        c) config=${OPTARG};;
        p) port=${OPTARG};;
        h) host=${OPTARG};;
        d) db=${OPTARG};;
        w) workers=${OPTARG};;
    esac
done

workers=${workers:-2}

export NEDREX_CONFIG=$config
#rq worker --url redis://$host:$port/$db default

for ((i=1; i<=workers; i++))
do
    rq worker --url redis://$host:$port/$db default &
done

# Wait for all background processes to finish
wait