import os
import time
import threading
import socket
# from pydub import AudioSegment
# from pydub.playback import play
from contextlib import contextmanager

SOCKDIR = os.environ.get("XDG_RUNTIME_DIR", "/var/tmp")
SOCKFILE = os.path.join(SOCKDIR, "polypomo.sock")

home_dir = os.environ.get('HOME', "/home/tester")
base_path = os.path.join(home_dir, "audio/other/alarmer/")

def playAudio(path):
    os.system("ffplay -nodisp -autoexit {} >/dev/null 2>&1".format(path))

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

def action_switch():
    # TODO logging = print("Running toggle", args)
    with setup_client() as s:
        msg = "audio_finished"
        s.send(msg.encode("utf8"))


def notify(path, signal=True):
    try:
        new_thread = threading.Thread(target=notify_wrapper, args=[path, signal])
        new_thread.daemon = True
        new_thread.start()
    except KeyboardInterrupt:
        print("interrupted by Ctrl-c 2")
        return

def playAsyncAudio(path):
    try:
        new_thread = threading.Thread(target=playAudio, args=[path])
        new_thread.daemon = True
        new_thread.start()
    except KeyboardInterrupt:
        print("interrupted by Ctrl-c 2")
        return

def notify_wrapper(path, signal):
    playAudioWithOtherMuted(path)
    if signal:
        action_switch()


def playAudioWithOtherMuted(file_name):
    """
    Mutes all the sounds, then plays syncronously the audio file given in path.
    """
    path = base_path + file_name

    audios = getAudioInfo()
    decreaseVolume(audios)
    changeGeneralVolume(audios, 50)
    time.sleep(0.5)

    playAudio(path)
    returnVolume(audios)

    print("Stopped")


def getAudioInfo():
    f = os.popen('pulsemixer --list-sinks')
    now = f.read()

    audios = []
    arr = now.splitlines()

    for line in arr:
        volume = line[line.index("Volumes:") + 9: -1]
        audio = {
            "id": line[line.index("ID:") + 4: line.index(",",
                                                         line.index("ID:"))],
            "name": line[line.index("Name:") + 6: line.index(",",
                                                             line.index("Name:"))],
            "volume": volume[volume.index("\'") + 1: volume.index("%",
                                                                  volume.index("\'"))],
        }
        audios.append(audio)
    return audios

# sink - audio["id"]
# value - what value change sink to


def changeSinkVolume(sink, value):
    os.system("pulsemixer --id {sink} --set-volume {value}".
              format(sink=sink, value=value))


def decreaseVolume(audios):
    for audio in audios:
        if audio["name"] != "Built-in Audio Analog Stereo" and audio["name"] != "Shapes":
            changeSinkVolume(audio["id"], 3)


def changeGeneralVolume(audios, value):
    for audio in audios:
        if audio["name"] == "Built-in Audio Analog Stereo":
            changeSinkVolume(audio["id"], value)
            break


def returnVolume(audios):
    for audio in audios:
        os.system("pulsemixer --id {sink} --set-volume {volume}".
                  format(sink=audio["id"], volume=audio["volume"]))
    
