"""The night bot's starter as an Azure Function (Python, v2 programming model): every five
minutes, starts the VM if a job waits for its runners. The logic is ``starter.py``'s."""

import json
import logging

import azure.functions as func

import starter

app = func.FunctionApp()


@app.timer_trigger(schedule="0 */5 * * * *", arg_name="timer", run_on_startup=False,
                   use_monitor=False)
def nightbot_starter(timer: func.TimerRequest) -> None:
    logging.info(json.dumps(starter.tick()))
