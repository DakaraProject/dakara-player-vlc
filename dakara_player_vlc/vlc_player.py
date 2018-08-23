import os
import logging
import urllib
from threading import Timer
from pkg_resources import parse_version

import vlc

from dakara_player_vlc.version import __version__
from dakara_player_vlc.safe_workers import Worker
from dakara_player_vlc.resources_manager import get_background


TRANSITION_DURATION = 2
TRANSITION_BG_NAME = "transition.png"


IDLE_DURATION = 60
IDLE_BG_NAME = "idle.png"


logger = logging.getLogger("vlc_player")


class VlcPlayer(Worker):
    """Interface for the Python VLC wrapper

    This class allows to manipulate VLC for complex tasks. It manages the
    display of idle/transition screens when playing, manages the VLC callbacks
    and provides some accessors and mutators to get/set the status of the
    player.

    The playlist is virtually handled using song-end callbacks.
    """
    def init_worker(self, config, text_generator):
        """Init the worker
        """
        self.config = config
        self.text_generator = text_generator

        config_vlc = config.get('vlc') or {}

        # parameters used to create the player instance
        fullscreen = config.get('fullscreen', False)
        instance_parameters = config_vlc.get('instance_parameters') or []

        # parameters that will be used later on
        self.kara_folder_path = config.get('kara_folder', "")
        self.media_parameters = config_vlc.get('media_parameters') or []
        self.media_parameters_text_screen = []

        # parameters for transition screen
        self.transition_duration = config.get(
            'transition_duration', TRANSITION_DURATION
        )

        # load backgrounds
        config_backgrounds = config.get('backgrounds') or {}
        custom_background_directory = config_backgrounds.get('directory', "")

        self.load_transition_bg_path(
            custom_background_directory,
            config_backgrounds.get('transition_background_name',
                                   TRANSITION_BG_NAME)
        )

        self.load_idle_bg_path(
            custom_background_directory,
            config_backgrounds.get('idle_background_name',
                                   IDLE_BG_NAME)
        )

        # playlist entry id of the current song
        # if no songs are playing, its value is None
        self.playing_id = None

        # flag set to True is a transition screen is playing
        self.in_transition = False

        # media containing a song which will be played after the transition
        # screen
        self.media_pending = None

        # VLC objects
        self.instance = vlc.Instance(instance_parameters)
        self.player = self.instance.media_player_new()
        self.player.set_fullscreen(fullscreen)
        self.event_manager = self.player.event_manager()
        self.media_list = self.instance.media_list_new()
        self.list_player = self.instance.media_list_player_new()
        self.list_player.set_media_list(self.media_list)
        self.list_player.set_media_player(self.player)
        # Add black screen media at the top of the list
        # And loop, so that this black screen is played when media ends
        # This ensure there is always something playing
        media_black_screen = self.instance.media_new_path(
            self.idle_bg_path
        )
        media_black_screen.add_options(
            *self.media_parameters,
            "image-duration={}".format(100000),
        )
        self.media_list.add_media(media_black_screen)
        self.list_player.set_playback_mode(vlc.PlaybackMode.loop)

        # VLC version
        self.vlc_version = vlc.libvlc_get_version().decode()
        logger.info("VLC %s", self.vlc_version)
        version_str, _ = self.vlc_version.split()
        version = parse_version(version_str)

        # perform action according to VLC version
        if version >= parse_version('3.0.0'):
            # starting from version 3, VLC prioritizes subtitle files that are
            # nearby the media played, not the ones explicitally added; this
            # option forces VLC to use the explicitally added files only
            self.media_parameters_text_screen.append("no-sub-autodetect-file")

        # timer for VLC taking too long to stop
        self.timer_stop_player_too_long = None

    def load_transition_bg_path(self, bg_directory_path, transition_bg_name):
        """Load transition backgound file path

        Load the customized background path or the default background path for
        the transition screen.

        Called once by the constructor.

        Args:
            bg_directory_path (str): path to the background directory.
        """
        dir_exists = os.path.isdir(bg_directory_path)
        dir_content = os.listdir(bg_directory_path) if dir_exists else []

        if transition_bg_name in dir_content:
            bg_path = os.path.join(bg_directory_path, transition_bg_name)
            logger.debug(
                "Loading custom transition background file '{}'".format(
                    transition_bg_name
                )
            )

        else:
            logger.debug("Loading default transition background file")

            if TRANSITION_BG_NAME in dir_content:
                bg_path = os.path.join(bg_directory_path, TRANSITION_BG_NAME)

            else:
                bg_path = get_background(TRANSITION_BG_NAME)

        self.transition_bg_path = bg_path

    def load_idle_bg_path(self, bg_directory_path, idle_bg_name):
        """Load idle backgound file path

        Load the customized background path or the default background path for
        the idle screen.

        Called once by the constructor.

        Args:
            bg_directory_path (str): path to the background directory.
        """
        dir_exists = os.path.isdir(bg_directory_path)
        dir_content = os.listdir(bg_directory_path) if dir_exists else []

        if idle_bg_name in dir_content:
            bg_path = os.path.join(bg_directory_path, idle_bg_name)
            logger.debug(
                "Loading custom idle background file '{}'".format(
                    idle_bg_name
                )
            )

        else:
            logger.debug("Loading default idle background file")

            if IDLE_BG_NAME in dir_content:
                bg_path = os.path.join(bg_directory_path, IDLE_BG_NAME)

            else:
                bg_path = get_background(IDLE_BG_NAME)

        self.idle_bg_path = bg_path

    def set_song_end_callback(self, callback):
        """Assign callback for when player reachs the end of current song

        Args:
            callback (function): function to assign.
        """
        self.event_manager.event_attach(
            vlc.EventType.MediaPlayerEndReached,
            self.song_end_callback
        )

        self.song_end_external_callback = callback

    def song_end_callback(self, event):
        """Callback called when song end reached occurs

        This can happen when a transition screen ends, leading to playing the
        actual song file, or when the song file ends, leading to calling the
        callback set by `set_song_end_callback`. A new thread created in any
        case.

        Args:
            event (vlc.EventType): VLC event object.
        """
        logger.debug("Song end callback called")

        if self.in_transition:
            # if the transition screen has finished,
            # request to play the song itself
            self.in_transition = False
            thread = self.create_thread(
                target=self.play_media,
                args=(self.media_pending, )
            )

            # get file path
            file_path = mrl_to_path(self.media_pending.get_mrl())
            logger.info("Now playing \"{}\"".format(
                file_path
            ))

        else:
            # otherwise, the song has finished,
            # so do what should be done
            thread = self.create_thread(target=self.song_end_external_callback)

        thread.start()

    def set_error_callback(self, callback):
        """Assign callback for when error occured

        Args:
            callback (function): function to assign.
        """
        self.event_manager.event_attach(
            vlc.EventType.MediaPlayerEncounteredError,
            self.error_callback
        )

        self.error_external_callback = callback

    def error_callback(self, event):
        """Callback called when error occurs

        Try to get error message and then call the callback set by
        `set_error_callback`.

        Args:
            event (vlc.EventType): VLC event object.
        """
        logger.debug("Error callback called")

        # according to this post in the VLC forum
        # (https://forum.videolan.org/viewtopic.php?t=90720), it is very
        # unlikely that any error message will be caught this way
        error_message = vlc.libvlc_errmsg() or \
            "No details, consult player logs"

        if isinstance(error_message, bytes):
            error_message = error_message.decode()

        thread = self.create_thread(
            target=self.error_external_callback,
            args=(
                self.playing_id,
                error_message
            )
        )

        self.playing_id = None
        self.in_transition = False
        thread.start()

    def play_media(self, media):
        """Play the given media

        Args:
            media (vlc.Media): VLC media object.
        """
        self.media_list.add_media(media)
        self.list_player.play_item(media)

    def play_song(self, playlist_entry):
        """Play music specified

        Prepare the media containing the music to play and store it. Add a
        transition screen and play it first. When the transition screen ends,
        the media will be played.

        Args:
            playlist_entry (dict): dictionnary containing at least `id` and
                `song` attributes. `song` is a dictionary containing at least
                the key `file_path`.
        """
        # file location
        file_path = os.path.join(
            self.kara_folder_path,
            playlist_entry["song"]["file_path"]
        )

        # Check file exists
        if not os.path.isfile(file_path):
            self.error_external_callback(
                playlist_entry['id'],
                "File not found \"{}\"".format(file_path)
            )
            return

        # create the media
        self.playing_id = playlist_entry["id"]
        self.media_pending = self.instance.media_new_path(file_path)
        self.media_pending.add_options(*self.media_parameters)

        # create the transition screen
        transition_text_path = self.text_generator.create_transition_text(
            playlist_entry
        )

        media_transition = self.instance.media_new_path(
            self.transition_bg_path
        )

        media_transition.add_options(
            *self.media_parameters_text_screen,
            *self.media_parameters,
            "sub-file={}".format(transition_text_path),
            "image-duration={}".format(self.transition_duration)
        )
        self.in_transition = True

        self.play_media(media_transition)
        logger.info("Playing transition for \"{}\"".format(file_path))

    def play_idle_screen(self):
        """Play idle screen
        """
        # set idle status
        self.playing_id = None
        self.in_transition = False

        # create idle screen media
        media = self.instance.media_new_path(
            self.idle_bg_path
        )

        # create the idle screen
        idle_text_path = self.text_generator.create_idle_text({
            'notes': [
                "VLC " + self.vlc_version,
                "Dakara player " + __version__
                ]
            })

        media.add_options(
            *self.media_parameters_text_screen,
            *self.media_parameters,
            "image-duration={}".format(IDLE_DURATION),
            "sub-file={}".format(idle_text_path),
        )

        self.play_media(media)
        logger.debug("Playing idle screen")

    def is_idle(self):
        """Get player idling status

        Returns:
            bool: False when playing a song or a transition screen, True when
                playing idle screen.
        """
        return self.playing_id is None

    def get_playing_id(self):
        """Playlist entry ID getter

        Returns:
            int: current playing ID or None when no song is playing.
        """
        return self.playing_id

    def get_timing(self):
        """Player timing getter

        Returns:
            int: current song timing if a song is playing or 0 when idle or
                during transition screen.
        """
        if self.is_idle() or self.in_transition:
            return 0

        timing = self.player.get_time()

        # correct the way VLC handles when it hasn't started to play yet
        if timing == -1:
            timing = 0

        return timing

    def is_paused(self):
        """Player pause status getter

        Returns:
            bool: True when playing song is paused.
        """
        return self.player.get_state() == vlc.State.Paused

    def set_pause(self, pause):
        """Pause or unpause the player

        Pause playing song when True unpause when False.

        Args:
            pause (bool): flag for pause state requested.
        """
        if not self.is_idle():
            if pause:
                logger.info("Setting pause")
                self.player.pause()
                logger.debug("Set pause")

            else:
                logger.info("Resuming play")
                self.player.play()
                logger.debug("Resumed play")

    def stop_player(self):
        """Stop playing music
        """
        logger.info("Stopping player")

        # send a warning within 3 seconds if VLC has not stopped already
        self.timer_stop_player_too_long = Timer(
            3, self.warn_stop_player_too_long
        )

        self.timer_stop_player_too_long.start()
        self.player.stop()
        logger.debug("Stopped player")

    @staticmethod
    def warn_stop_player_too_long():
        """Notify the user that VLC takes too long to stop
        """
        logger.warning("VLC takes too long to stop")

    def exit_worker(self, exception_type, exception_value, traceback):
        """Exit the worker
        """
        self.stop_player()

        # clear the warning message if any
        if self.timer_stop_player_too_long:
            self.timer_stop_player_too_long.cancel()


def mrl_to_path(file_mrl):
    """Convert a MRL to a classic path

    File path is stored as MRL inside a media object, we have to bring it back
    to a more classic looking path format.

    Args:
        file_mrl (str): path to the resource with MRL format.
    """
    file_mrl_parsed = urllib.parse.urlparse(file_mrl)
    return urllib.parse.unquote(file_mrl_parsed.path)
