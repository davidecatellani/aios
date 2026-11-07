#!/usr/bin/env python3
"""Esegue una sessione, interrompendo il gruppo di processi prima di spegnere il PC."""

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time


PAUSED = 75


def stop_group(process, grace):
    # Anche i compilatori/git figli ricevono il segnale, non soltanto bash.
    for sig, wait in ((signal.SIGINT, grace), (signal.SIGTERM, grace), (signal.SIGKILL, 5)):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            break
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            process.poll()
            # Nel contenitore siamo PID 1: raccogli gli eventuali figli rimasti orfani.
            if process.returncode is not None and os.getpid() == 1:
                try:
                    while os.waitpid(-1, os.WNOHANG)[0]:
                        pass
                except ChildProcessError:
                    pass
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.1)
    process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, required=True)
    parser.add_argument('--grace-seconds', type=float, default=45)
    parser.add_argument('--result', type=Path, required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if args.seconds < 0 or args.grace_seconds < 0 or not command:
        parser.error('Durata non negativa e comando obbligatorio')

    interrupted = []
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda signum, frame: interrupted.append(signum))
    started = time.monotonic()

    def result(state, reason, code):
        doc = {'state': state, 'reason': reason, 'exit_code': code,
               'elapsed_seconds': round(time.monotonic() - started)}
        args.result.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.result.with_suffix('.tmp')
        temporary.write_text(json.dumps(doc) + '\n')
        temporary.replace(args.result)
        return code

    # Sostituisce anche un eventuale risultato precedente: nessun errore viene
    # interpretato come pausa sulla base di un file rimasto da un'altra esecuzione.
    result('running', '', 0)
    try:
        process = subprocess.Popen(command, start_new_session=True)
    except OSError as error:
        print(f'Impossibile avviare la sessione: {error}', flush=True)
        return result('failed', 'start_error', 127)
    while True:
        code = process.poll()
        if code is not None:
            code = code if code >= 0 else 128 - code
            return result('completed' if code == 0 else 'failed', 'command_exit', code)
        if interrupted or (args.seconds and time.monotonic() - started >= args.seconds):
            reason = 'interrupted' if interrupted else 'time_limit'
            print('Arresto della sessione: attendo la chiusura dei processi; cache e build restano sul disco.', flush=True)
            stop_group(process, args.grace_seconds)
            if interrupted:
                return result('paused', reason, 128 + interrupted[0])
            print('PAUSA PROGRAMMATA: rilancia la stessa fase per continuare. L’immagine non è ancora pronta.', flush=True)
            return result('paused', reason, PAUSED)
        time.sleep(0.1)


if __name__ == '__main__':
    raise SystemExit(main())
