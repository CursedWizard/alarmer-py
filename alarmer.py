#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import socket
import argparse
import random
import operator
import time
import select
import audioNotifier as an
from contextlib import contextmanager
from subprocess import call, DEVNULL
from dateutil import parser
from typing import Literal, Union
from datetime import datetime, timedelta

SOCKDIR = os.environ.get("XDG_RUNTIME_DIR", "/var/tmp")
SOCKFILE = os.path.join(SOCKDIR, "polypomo.sock")
TOMATO = "work"
BREAK = "break"

class GnomeNotifyEvent:
    def notify(self, title: str, message: str, type: Literal["low", "normal", "critical"] = "low"):
        call(["notify-send", "-u", type, title, message])

class RandomTimeEventTrigger:
    def __init__(self, event, minimum_time_between_events: timedelta = timedelta(minutes=25),
            random_time_addition: int = 20):
        self.minimum_time_between_events = minimum_time_between_events
        self.random_time_addition = timedelta(minutes=random.randint(0, random_time_addition))
        self.next_time_event = self._get_next_time_()
        self.event = event

    def _get_next_time_(self):
        return datetime.now() + self.minimum_time_between_events + self.random_time_addition

    def triggerIfGood(self):
        if datetime.now() > self.next_time_event:
            self.next_time_event = self._get_next_time_()
            self.event.notify("Hydration alert!", "Are you stayng hydrated?")
            return True
        return False

class Exit(Exception):
    pass

class SignalEvent:
    def __init__(self, _event_time, _audio_name: str, _audio_number: int):
        # parsed_time = parser.parse("23:30")
        parsed_time = parser.parse(_event_time)
        self.event_time = parsed_time.timestamp()
        self.notified = False

        self.audio_name = _audio_name
        self.audio_number = _audio_number

    def triggerIfGood(self):
        now = time.time()

        if not self.notified and self.event_time <= now:
            path = "{name}_{num}.wav".format(
                name=self.audio_name,
                num=random.randint(1, self.audio_number)
            )
            an.notify(path, False)
            self.notified = True


class Timer:
    def __init__(self, remtime):
        self.time = remtime
        self.notified = False
        self.tick()

    def __str__(self):
        return self.format_time()

    def tick(self):
        self.previous = time.time()

    # returns time in format
    # +/-hh:mm:ss
    def format_time(self):
        day_factor = 86400
        hour_factor = 3600
        minute_factor = 60

        if self.time > 0:
            rem = self.time
            neg = ""
        else:
            rem = -self.time
            neg = "-"
        days = int(rem // day_factor)
        rem -= days * day_factor
        hours = int(rem // hour_factor)
        rem -= hours * hour_factor
        minutes = int(rem // minute_factor)
        rem -= minutes * minute_factor
        seconds = int(rem // 1)

        strtime = []
        if days > 0:
            strtime.append(str(days))
        if days > 0 or hours > 0:
            strtime.append("{:02d}".format(hours))

        # Always append minutes and seconds
        strtime.append("{:02d}".format(minutes))
        strtime.append("{:02d}".format(seconds))

        return neg + ":".join(strtime)

    def update(self):
        now = time.time()
        delta = now - self.previous
        self.time -= delta

        # Send a notification when timer reaches 0
        if not self.notified and self.time < 0:
            self.notified = True
            # timer has finished
            return True

        return False

    def change(self, op, seconds):
        self.time = op(self.time, seconds)


class Status:
    def __init__(self, worktime, breaktime):
        self.worktime = worktime
        self.breaktime = breaktime
        self.status = "work"  # or "break"
        self.timer = Timer(self.worktime)
        self.active = True
        self.locked = True
        self.audio_playing = "off"

        self.round_number = 0
        self.sleep_event = SignalEvent("23:30", "sleep", 3)


    def show_status(self):
        sys.stdout.write("{}\n".format(self.status))
        sys.stdout.flush()

    def show(self, conn):
        status = self.status
        mode = "on" if self.active else "off"
        percent = int(100 * self.timer.time /
                      (self.worktime if self.status == "work" else self.breaktime))
        msg = "{} {} {} {} {}".format(mode, status, self.timer, percent, self.round_number)
        conn.sendall(msg.encode("utf8"))

    def toggle(self):
        self.active = not self.active

    def toggle_lock(self):
        self.locked = not self.locked

    def update(self):
        if self.active:
            timer_finished = self.timer.update()
            if timer_finished:
                _name, _num = ("work", 14) if self.status == "break" else ("rest", 8)
                path = "{name}_{num}.wav".format(
                    name=_name,
                    num=random.randint(1,_num)
                )
                an.notify(path)
                self.audio_playing = "playing"
                self.active = False

        # constantly updating timer
        if self.audio_playing == "finished":
            if self.status == "break":
                self.round_number += 1
            self.next_timer()
            self.toggle()
            self.audio_playing = "off"

        self.sleep_event.triggerIfGood()
        # This ensures the timer counts time since the last iteration
        # and not since it was initialized
        self.timer.tick()

    def change(self, op, seconds):
        if self.locked:
            return

        seconds = int(seconds)
        op = operator.add if op == "add" else operator.sub
        self.timer.change(op, seconds)

    def audioFinished(self):
        self.audio_playing = "finished"

    def next_timer(self):
        self.active = False

        if self.status == "work":
            self.status = "break"
            self.timer = Timer(self.breaktime)
        elif self.status == "break":
            self.status = "work"
            self.timer = Timer(self.worktime)


@contextmanager
def setup_listener():
    # If there's an existing socket, tell the other to exit and replace it
    action_exit(None)

    # If there is a socket on disk after sending an exit request, delete it
    try:
        os.remove(SOCKFILE)
    except FileNotFoundError:
        pass

    # setting up TCP socket
    s = socket.socket(socket.AF_UNIX,
                      socket.SOCK_STREAM)
    s.bind(SOCKFILE)
    s.listen()
    s.settimeout(0.2) # timeout for listening

    try:
        yield s
    finally:
        s.close()
        # Don't try to delete the socket since at this point it could
        # be owned by a different process
        # try:
        #     os.remove(SOCKFILE)
        # except FileNotFoundError:
        #     pass


@contextmanager
def setup_client():
    # creates socket object
    s = socket.socket(socket.AF_UNIX,
                      socket.SOCK_STREAM)

    s.connect(SOCKFILE)

    try:
        yield s
    finally:
        s.close()

    # tm = s.recv(1024)  # msg can only be 1024 bytes long


def wait_for_socket_cleanup(tries=20, wait=0.5):
    for i in range(tries):
        if not os.path.isfile(SOCKFILE):
            return True
        else:
            time.sleep(wait)

    return False


# check for request from client and
# call corresponding functions
def check_actions(sock: socket.socket , status: Status):
    timeout = time.time() + 0.9

    data = ""

    connection = None
    while True:
        if time.time() > timeout:
            break
        # readable, writable, errored = select.select(read_list, [], [])
        try:
            connection, client_address = sock.accept()
            data = connection.recv(1024)
            if data:
                break
        except socket.timeout as e:
            pass
            # TODO replace this by logging
            # print('Lost connection to client. Printing buffer...', e)
            # break

    if not data:
        return

    action = data.decode("utf8")
    # print("New action " + action)
    if action == "toggle":
        status.toggle()
    if action == "audio_finished":
        status.audioFinished()
    elif action == "switch":
        status.next_timer()
        status.toggle()
    elif action == "gstatus":
        status.show_status()
    elif action == "gtime":
        status.show(connection)
    elif action == "end":
        status.next_timer()
    elif action == "lock":
        status.toggle_lock()
    elif action.startswith("time"):
        _, op, seconds = action.split(" ")
        status.change(op, seconds)
    elif action == "exit":
        raise Exit()
    connection.close()


def action_display(args):
    # TODO logging = print("Running display", args)

    status = Status(args.worktime, args.breaktime)
    hydrationNotifier = RandomTimeEventTrigger(GnomeNotifyEvent())

    # Listen on socket
    with setup_listener() as sock:
        while True:
            status.update()
            hydrationNotifier.triggerIfGood()
            try:
                check_actions(sock, status)
            except Exit:
                print("Received exit request...")
                break

def action_switch(args):
    with setup_client() as s:
        msg = "switch"
        s.send(msg.encode("utf8"))

def action_get_status(args):
    with setup_client() as s:
        msg = "gstatus"
        s.send(msg.encode("utf8"))

def action_get_time(args):
    with setup_client() as s:
        msg = "gtime"
        s.sendall(msg.encode("utf8"))
        data = s.recv(1024)
        action = data.decode("utf8")
        print(action)

def action_toggle(args):
    # TODO logging = print("Running toggle", args)
    with setup_client() as s:
        msg = "toggle"
        s.sendall(msg.encode("utf8"))


def action_end(args):
    # TODO logging = print("Running end", args)
    with setup_client() as s:
        msg = "end"
        s.send(msg.encode("utf8"))


def action_lock(args):
    # TODO logging = print("Running lock", args)
    with setup_client() as s:
        msg = "lock"
        s.send(msg.encode("utf8"))


def action_time(args):
    # TODO logging = print("Running time", args)
    with setup_client() as s:
        msg = "time " + " ".join(args.delta)
        s.send(msg.encode("utf8"))


def action_exit(args):
    # TODO logging = print("Running exit", args)
    try:
        with setup_client() as s:
            msg = "exit"
            s.send(msg.encode("utf8"))
    except (FileNotFoundError, ConnectionRefusedError) as e:
        print("No instance is listening, error:", e)
    else:
        if not wait_for_socket_cleanup():
            print("Socket was not removed, assuming it's stale")


class ValidateTime(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        if values[0] not in '-+':
            parser.error("Time format should be +num or -num to add or remove time, respectively")
        if not values[1:].isdigit():
            parser.error("Expected number after +/- but saw '{}'".format(values[1:]))

        # action = operator.add if values[0] == '+' else operator.sub
        # value = int(values[1:])
        action = "add" if values[0] == '+' else "sub"
        value = values[1:]

        setattr(namespace, self.dest, (action, value))


def parse_args():
    parser = argparse.ArgumentParser(
        description="Pomodoro timer to be used with polybar")
    # Display - main loop showing status
    parser.add_argument("--worktime",
                        type=int,
                        default=50 * 60,
                        help="Default work timer time in seconds")
    parser.add_argument("--breaktime",
                        type=int,
                        default=10 * 60,
                        help="Default break timer time in seconds")
    parser.set_defaults(func=action_display)

    sub = parser.add_subparsers()

    auto_switch = sub.add_parser("switch",
                            help="get full info")
    auto_switch.set_defaults(func=action_get_time)

    # get time
    show = sub.add_parser("show",
                            help="get full info")
    show.set_defaults(func=action_get_time)

    # get time
    gtime = sub.add_parser("gtime",
                            help="get current time fromatted")
    gtime.set_defaults(func=action_get_time)

    # get status
    gstatus = sub.add_parser("gstatus",
                            help="get current status")
    gstatus.set_defaults(func=action_get_status)

    # start/stop timer
    toggle = sub.add_parser("toggle",
                            help="start/stop timer")
    toggle.set_defaults(func=action_toggle)

    # end timer
    end = sub.add_parser("end",
                         help="end current timer")
    end.set_defaults(func=action_end)

    # lock timer changes
    lock = sub.add_parser("lock",
                          help="lock time actions - prevent changing time")
    lock.set_defaults(func=action_lock)

    # lock timer changes
    exit = sub.add_parser("exit",
                          help="exit any listening polypomo instances gracefully")
    exit.set_defaults(func=action_exit)

    # change timer
    time = sub.add_parser("time",
                          help="add/remove time to current timer")
    time.add_argument("delta",
                      action=ValidateTime,
                      help="Time to add/remove to current timer (in seconds)")
    time.set_defaults(func=action_time)

    return parser.parse_args()


def main():
    args = parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

# vim: ai sts=4 et sw=4
