import re
from tempfile import gettempdir
from contextlib import ExitStack
from queue import Queue
from threading import Event
from time import sleep
from unittest import TestCase
from unittest.mock import MagicMock, patch, call

import vlc
from packaging.version import parse
from path import Path

from dakara_player.media_player.vlc import (
    MediaPlayerVlc,
    VlcTooOldError,
)
from dakara_player.media_player.base import (
    KaraFolderNotFound,
    InvalidStateError,
    VersionNotFoundError,
)
from dakara_player.mrl import mrl_to_path, path_to_mrl
from dakara_player.text_generator import TextGenerator


@patch("dakara_player.media_player.base.PATH_BACKGROUNDS", "bg")
@patch("dakara_player.media_player.base.TRANSITION_DURATION", 10)
@patch("dakara_player.media_player.base.IDLE_DURATION", 20)
class MediaPlayerVlcTestCase(TestCase):
    """Test the VLC player class unitary
    """

    def setUp(self):
        # create playlist entry ID
        self.id = 42

        # create playlist entry file path
        self.song_file_path = Path("file")

        # create playlist entry
        self.playlist_entry = {
            "id": self.id,
            "song": {"title": "Song title", "file_path": self.song_file_path},
            "owner": "me",
            "use_instrumental": False,
        }

    def get_instance(
        self,
        config=None,
        mock_instance=True,
        mock_background_loader=True,
        mock_text_generator=True,
    ):
        """Get a heavily mocked instance of MediaPlayerVlc

        Args:
            config (dict): Configuration passed to the constructor.
            mock_instance (bool): If True, the VLC Instance class is mocked,
                otherwise it is a real object.
            mock_background_loader(bool): If True, the BackgroundLoader class
                is mocked, otherwise it is a real object.
            mock_text_generator(bool): If True, the TextGenerator class is
                mocked, otherwise it is a real object.

        Returns:
            tuple: Contains the following elements:
                MediaPlayerVlc: Instance;
                tuple: Contains the mocked objects:
                    unittest.mock.MagicMock: VLC Instance object or None if
                        `mock_instance` is False;
                    unittest.mock.MagicMock: BackgroundLoader object or None if
                        `mock_background_loader` is False;
                    unittest.mock.MagicMock: TextGenerator object or None if
                        `mock_text_generator` is False;
                tuple: Contains the mocked classes:
                    unittest.mock.MagicMock: VLC Instance class or None if
                        `mock_instance` is False;
                    unittest.mock.MagicMock: BackgroundLoader class or None if
                        `mock_background_loader` is False;
                    unittest.mock.MagicMock: TextGenerator class or None if
                        `mock_text_generator` is False.
        """
        config = config or {"kara_folder": gettempdir()}

        with ExitStack() as stack:
            mocked_instance_class = (
                stack.enter_context(
                    patch("dakara_player.media_player.vlc.vlc.Instance")
                )
                if mock_instance
                else None
            )

            mocked_background_loader_class = (
                stack.enter_context(
                    patch("dakara_player.media_player.base.BackgroundLoader")
                )
                if mock_background_loader
                else None
            )

            mocked_text_generator_class = (
                stack.enter_context(
                    patch("dakara_player.media_player.base.TextGenerator")
                )
                if mock_text_generator
                else None
            )

            return (
                MediaPlayerVlc(Event(), Queue(), config, Path("temp")),
                (
                    mocked_instance_class.return_value
                    if mocked_instance_class
                    else None,
                    mocked_background_loader_class.return_value
                    if mocked_background_loader_class
                    else None,
                    mocked_text_generator_class.return_value
                    if mocked_text_generator_class
                    else None,
                ),
                (
                    mocked_instance_class,
                    mocked_background_loader_class,
                    mocked_text_generator_class,
                ),
            )

    def set_playlist_entry(self, vlc_player, started=True):
        """Set a playlist entry and make the player play it

        Args:
            vlc_player (MediaPlayerVlc): Instance of the VLC player.
            started (bool): If True, make the player play the song.
        """
        vlc_player.playlist_entry = self.playlist_entry

        # create mocked transition
        vlc_player.playlist_entry_data["transition"].media = MagicMock()

        # create mocked song
        media_song = MagicMock()
        media_song.get_mrl.return_value = path_to_mrl(
            vlc_player.kara_folder_path / self.playlist_entry["song"]["file_path"]
        )
        vlc_player.playlist_entry_data["song"].media = media_song

        # set media has started
        if started:
            player = vlc_player.instance.media_player_new.return_value
            player.get_media.return_value = vlc_player.playlist_entry_data["song"].media
            vlc_player.playlist_entry_data["transition"].started = True
            vlc_player.playlist_entry_data["song"].started = True

    def test_set_callback(self):
        """Test the assignation of a callback
        """
        # create instance
        vlc_player, _, _ = self.get_instance()

        # create a callback function
        callback = MagicMock()

        # pre assert the callback is not set yet
        self.assertIsNot(vlc_player.callbacks.get("test"), callback)

        # call the method
        vlc_player.set_callback("test", callback)

        # post assert the callback is now set
        self.assertIs(vlc_player.callbacks.get("test"), callback)

    def test_set_vlc_callback(self):
        """Test the assignation of a callback to a VLC event

        We have also to mock the event manager method because there is no way
        with the VLC library to know which callback is associated to a given
        event.
        """
        # create instance
        vlc_player, _, _ = self.get_instance()

        # patch the event creator
        vlc_player.event_manager.event_attach = MagicMock()

        # create a callback function
        callback = MagicMock()

        # pre assert the callback is not set yet
        self.assertIsNot(
            vlc_player.vlc_callbacks.get(vlc.EventType.MediaPlayerEndReached), callback
        )

        # call the method
        vlc_player.set_vlc_callback(vlc.EventType.MediaPlayerEndReached, callback)

        # assert the callback is now set
        self.assertIs(
            vlc_player.vlc_callbacks.get(vlc.EventType.MediaPlayerEndReached), callback
        )

        # assert the event manager got the right arguments
        vlc_player.event_manager.event_attach.assert_called_with(
            vlc.EventType.MediaPlayerEndReached, callback
        )

    @patch("dakara_player.media_player.vlc.vlc.libvlc_get_version", autospec=True)
    def test_get_version_long_4_digits(self, mocked_libvlc_get_version):
        """Test to get the VLC version when it is long and contains 4 digits
        """
        # mock the version of VLC
        mocked_libvlc_get_version.return_value = b"3.0.11.1 Vetinari"

        # call the method
        version = MediaPlayerVlc.get_version()

        # assert the result
        self.assertEqual(version, parse("3.0.11.1"))

    @patch("dakara_player.media_player.vlc.vlc.libvlc_get_version", autospec=True)
    def test_get_version_long(self, mocked_libvlc_get_version):
        """Test to get the VLC version when it is long
        """
        # mock the version of VLC
        mocked_libvlc_get_version.return_value = b"3.0.11 Vetinari"

        # call the method
        version = MediaPlayerVlc.get_version()

        # assert the result
        self.assertEqual(version, parse("3.0.11"))

    @patch("dakara_player.media_player.vlc.vlc.libvlc_get_version", autospec=True)
    def test_get_version_not_found(self, mocked_libvlc_get_version):
        """Test to get the VLC version when it is not available
        """
        # mock the version of VLC
        mocked_libvlc_get_version.return_value = b"none"

        # call the method
        with self.assertRaisesRegex(VersionNotFoundError, "Unable to get VLC version"):
            MediaPlayerVlc.get_version()

    @patch.object(MediaPlayerVlc, "get_version")
    def test_check_version(self, mocked_get_version):
        """Test to check recent enough version VLC
        """
        # create instance
        vlc_player, _, _ = self.get_instance()

        # mock the version of VLC
        mocked_get_version.return_value = parse("3.0.0")

        # call the method
        vlc_player.check_version()

    @patch.object(MediaPlayerVlc, "get_version")
    def test_check_version_old(self, mocked_get_version):
        """Test to check old version of VLC
        """
        # create instance
        vlc_player, _, _ = self.get_instance()

        # mock the version of VLC
        mocked_get_version.return_value = parse("2.0.0")

        # call the method
        with self.assertRaisesRegex(VlcTooOldError, "VLC is too old"):
            vlc_player.check_version()

    @patch.object(Path, "exists")
    def test_check_kara_folder_path(self, mocked_exists):
        """Test to check if the kara folder exists
        """
        # create instance
        vlc_player, _, _ = self.get_instance()

        # pretend the directory exists
        mocked_exists.return_value = True

        # call the method
        vlc_player.check_kara_folder_path()

        # assert the call
        mocked_exists.assert_called_with()

    @patch.object(Path, "exists")
    def test_check_kara_folder_path_does_not_exist(self, mocked_exists):
        """Test to check if the kara folder does not exist
        """
        # create instance
        vlc_player, _, _ = self.get_instance()

        # pretend the directory does not exist
        mocked_exists.return_value = False

        # call the method
        with self.assertRaisesRegex(
            KaraFolderNotFound,
            'Karaoke folder "{}" does not exist'.format(re.escape(gettempdir())),
        ):
            vlc_player.check_kara_folder_path()

    @patch.object(MediaPlayerVlc, "check_kara_folder_path")
    @patch.object(MediaPlayerVlc, "check_version")
    @patch.object(MediaPlayerVlc, "set_vlc_default_callbacks")
    @patch.object(MediaPlayerVlc, "get_version")
    def test_load(
        self,
        mocked_get_version,
        mocked_set_vlc_default_callback,
        mocked_check_version,
        mocked_check_kara_folder_path,
    ):
        """Test to load the instance
        """
        # create instance
        (
            vlc_player,
            (_, mocked_background_loader, mocked_text_generator),
            _,
        ) = self.get_instance()

        # setup mocks
        mocked_get_version.return_value = "3.0.0 NoName"

        # call the method
        with self.assertLogs("dakara_player.media_player.vlc", "INFO") as logger:
            vlc_player.load()

        # assert the calls
        mocked_check_kara_folder_path.assert_called_with()
        mocked_text_generator.load.assert_called_with()
        mocked_background_loader.load.assert_called_with()
        mocked_check_version.assert_called_with()
        mocked_set_vlc_default_callback.assert_called_with()
        vlc_player.player.set_fullscreen.assert_called_with(False)

        # assert logs
        self.assertListEqual(
            logger.output, ["INFO:dakara_player.media_player.vlc:VLC 3.0.0 NoName"]
        )

    @patch.object(Path, "exists")
    def test_set_playlist_entry_error_file(self, mocked_exists):
        """Test to set a playlist entry that does not exist
        """
        # create instance
        vlc_player, _, _ = self.get_instance()

        # mock the system call
        mocked_exists.return_value = False

        # mock the callbacks
        vlc_player.set_callback("could_not_play", MagicMock())
        vlc_player.set_callback("error", MagicMock())

        # pre assertions
        self.assertIsNone(vlc_player.playlist_entry)

        # call the method
        with self.assertLogs("dakara_player.media_player.base", "DEBUG") as logger:
            vlc_player.set_playlist_entry(self.playlist_entry)

        # call assertions
        mocked_exists.assert_called_once_with()

        # post assertions
        self.assertIsNone(vlc_player.playlist_entry)

        # assert the callbacks
        vlc_player.callbacks["could_not_play"].assert_called_with(self.id)
        vlc_player.callbacks["error"].assert_called_with(self.id, "File not found")

        # assert the effects on logs
        self.assertListEqual(
            logger.output,
            [
                "ERROR:dakara_player.media_player.base:File not found '{}'".format(
                    Path(gettempdir()) / self.song_file_path
                )
            ],
        )

    @patch.object(MediaPlayerVlc, "manage_instrumental")
    @patch.object(MediaPlayerVlc, "play")
    @patch.object(MediaPlayerVlc, "generate_text")
    @patch.object(Path, "exists")
    def test_set_playlist_entry(
        self,
        mocked_exists,
        mocked_generate_text,
        mocked_play,
        mocked_manage_instrumental,
    ):
        """Test to set a playlist entry
        """
        # create instance
        vlc_player, (_, mocked_background_loader, _), _ = self.get_instance(
            mock_instance=False
        )

        # setup mocks
        mocked_exists.return_value = True
        mocked_background_loader.backgrounds = {
            "transition": Path(gettempdir()) / "transition.png"
        }

        # mock the callbacks
        vlc_player.set_callback("could_not_play", MagicMock())
        vlc_player.set_callback("error", MagicMock())

        # pre assertions
        self.assertIsNone(vlc_player.playlist_entry)

        # call the method
        vlc_player.set_playlist_entry(self.playlist_entry)
        self.assertFalse(self.playlist_entry["use_instrumental"])

        # post assertions
        self.assertDictEqual(vlc_player.playlist_entry, self.playlist_entry)
        data_transition = vlc_player.playlist_entry_data["transition"]
        self.assertEqual(
            mrl_to_path(data_transition.media.get_mrl()),
            Path(gettempdir()) / "transition.png",
        )

        # assert the callbacks
        vlc_player.callbacks["could_not_play"].assert_not_called()
        vlc_player.callbacks["error"].assert_not_called()

        # assert mocks
        mocked_exists.assert_called_with()
        mocked_generate_text.assert_called_with("transition")
        mocked_play.assert_called_with("transition")
        mocked_manage_instrumental.assert_not_called()

    @patch.object(MediaPlayerVlc, "get_audio_tracks_id")
    @patch.object(MediaPlayerVlc, "get_number_tracks")
    @patch.object(MediaPlayerVlc, "get_instrumental_file")
    def test_manage_instrumental_file(
        self,
        mocked_get_instrumental_file,
        mocked_get_number_tracks,
        mocked_get_audio_tracks_id,
    ):
        """Test to add instrumental file
        """
        # create instance
        vlc_player, (mocked_instance, _, _), _ = self.get_instance()
        video_path = Path(gettempdir()) / "video"
        audio_path = Path(gettempdir()) / "audio"

        # pre assertions
        self.assertIsNone(vlc_player.playlist_entry_data["song"].audio_track_id)
        self.assertIsNotNone(vlc_player.kara_folder_path)

        # set playlist entry to request instrumental
        self.playlist_entry["use_instrumental"] = True

        # mocks
        mocked_get_instrumental_file.return_value = audio_path
        mocked_get_number_tracks.return_value = 2
        mocked_media_song = mocked_instance.media_new_path.return_value
        vlc_player.playlist_entry_data["song"].media = mocked_media_song

        # call the method
        with self.assertLogs("dakara_player.media_player.vlc", "DEBUG") as logger:
            vlc_player.manage_instrumental(self.playlist_entry, video_path)

        # post assertions
        self.assertEqual(vlc_player.playlist_entry_data["song"].audio_track_id, 2)

        # assert the effects on logs
        self.assertListEqual(
            logger.output,
            [
                "INFO:dakara_player.media_player.vlc:Requesting to play instrumental "
                "file '{}' for '{}'".format(audio_path, video_path),
            ],
        )

        # assert the call
        mocked_get_audio_tracks_id.assert_not_called()

    @patch.object(MediaPlayerVlc, "get_number_tracks")
    @patch.object(MediaPlayerVlc, "get_instrumental_file")
    def test_manage_instrumental_file_error_slaves_add(
        self, mocked_get_instrumental_file, mocked_get_number_tracks,
    ):
        """Test to be unable to add instrumental file
        """
        # create instance
        vlc_player, (mocked_instance, _, _), _ = self.get_instance()
        video_path = Path(gettempdir()) / "video"
        audio_path = Path(gettempdir()) / "audio"

        # pre assertions
        self.assertIsNone(vlc_player.playlist_entry_data["song"].audio_track_id)
        self.assertIsNotNone(vlc_player.kara_folder_path)

        # set playlist entry to request instrumental
        self.playlist_entry["use_instrumental"] = True

        # mocks
        mocked_get_instrumental_file.return_value = audio_path
        mocked_get_number_tracks.return_value = 2

        # make slaves_add method unavailable
        mocked_media_song = mocked_instance.return_value.media_new_path.return_value
        mocked_media_song.slaves_add.side_effect = NameError("no slaves_add")
        vlc_player.playlist_entry_data["song"].media = mocked_media_song

        # call the method
        with self.assertLogs("dakara_player.media_player.vlc", "DEBUG") as logger:
            vlc_player.manage_instrumental(self.playlist_entry, video_path)

        # post assertions
        self.assertIsNone(vlc_player.playlist_entry_data["song"].audio_track_id)

        # assert the effects on logs
        self.assertListEqual(
            logger.output,
            [
                "INFO:dakara_player.media_player.vlc:Requesting to play instrumental "
                "file '{}' for '{}'".format(audio_path, video_path),
                "ERROR:dakara_player.media_player.vlc:This version of VLC does "
                "not support slaves, cannot add instrumental file",
            ],
        )

    @patch.object(MediaPlayerVlc, "get_audio_tracks_id")
    @patch.object(MediaPlayerVlc, "get_number_tracks")
    @patch.object(MediaPlayerVlc, "get_instrumental_file")
    def test_manage_instrumental_track(
        self,
        mocked_get_instrumental_file,
        mocked_get_number_tracks,
        mocked_get_audio_tracks_id,
    ):
        """Test add instrumental track
        """
        # create instance
        vlc_player, (mocked_instance, _, _,), _ = self.get_instance()
        video_path = Path(gettempdir()) / "video"

        # pre assertions
        self.assertIsNone(vlc_player.playlist_entry_data["song"].audio_track_id)
        self.assertIsNotNone(vlc_player.kara_folder_path)

        # set playlist entry to request instrumental
        self.playlist_entry["use_instrumental"] = True

        # mocks
        mocked_get_instrumental_file.return_value = None
        mocked_get_audio_tracks_id.return_value = [0, 99, 42]
        mocked_media_song = mocked_instance.media_new_path.return_value
        vlc_player.playlist_entry_data["song"].media = mocked_media_song

        # call the method
        with self.assertLogs("dakara_player.media_player.vlc", "DEBUG") as logger:
            vlc_player.manage_instrumental(self.playlist_entry, video_path)

        # post assertions
        self.assertEqual(vlc_player.playlist_entry_data["song"].audio_track_id, 99)

        # assert the effects on logs
        self.assertListEqual(
            logger.output,
            [
                "INFO:dakara_player.media_player.vlc:Requesting to play instrumental "
                "track of '{}'".format(video_path),
            ],
        )

        # assert the call
        mocked_get_number_tracks.assert_not_called()

    @patch.object(MediaPlayerVlc, "get_audio_tracks_id")
    @patch.object(MediaPlayerVlc, "get_instrumental_file")
    def test_manage_instrumental_no_instrumental_found(
        self, mocked_get_instrumental_file, mocked_get_audio_tracks_id
    ):
        """Test to cannot find instrumental
        """
        # create instance
        vlc_player, (mocked_instance, _, _), _ = self.get_instance()
        video_path = Path(gettempdir()) / "video"

        # pre assertions
        self.assertIsNone(vlc_player.playlist_entry_data["song"].audio_track_id)

        # set playlist entry to request instrumental
        self.playlist_entry["use_instrumental"] = True

        # mocks
        mocked_get_instrumental_file.return_value = None
        mocked_get_audio_tracks_id.return_value = [99]

        # make slaves_add method unavailable
        mocked_media_song = mocked_instance.return_value.media_new_path.return_value
        vlc_player.playlist_entry_data["song"].media = mocked_media_song

        # call the method
        with self.assertLogs("dakara_player.media_player.vlc", "DEBUG") as logger:
            vlc_player.manage_instrumental(self.playlist_entry, video_path)

        # post assertions
        self.assertIsNone(vlc_player.playlist_entry_data["song"].audio_track_id)

        # assert the effects on logs
        self.assertListEqual(
            logger.output,
            [
                "WARNING:dakara_player.media_player.vlc:Cannot find instrumental "
                "file or track for file '{}'".format(video_path)
            ],
        )

    @patch.object(MediaPlayerVlc, "is_playing_this")
    def test_set_pause_idle(self, mocked_is_playing_this):
        """Test to set pause when the player is idle
        """
        # create instance
        vlc_player, (mocked_instance, _, _), _ = self.get_instance()
        player = mocked_instance.media_player_new.return_value

        # mock
        mocked_is_playing_this.return_value = True

        # call method
        vlc_player.pause(True)

        # assert call
        player.pause.assert_not_called()
        mocked_is_playing_this.assert_called_with("idle")

    @patch.object(MediaPlayerVlc, "create_thread")
    @patch.object(MediaPlayerVlc, "is_playing_this")
    def test_handle_end_reached_transition(
        self, mocked_is_playing_this, mocked_create_thread
    ):
        """Test song end callback after a transition screen
        """
        # create instance
        vlc_player, _, _ = self.get_instance()
        self.set_playlist_entry(vlc_player)
        vlc_player.playlist_entry_data["song"].started = False

        # mock the call
        mocked_is_playing_this.return_value = True
        vlc_player.set_callback("finished", MagicMock())

        # call the method
        with self.assertLogs("dakara_player.media_player.vlc", "DEBUG") as logger:
            vlc_player.handle_end_reached("event")

        # assert effect on logs
        self.assertListEqual(
            logger.output,
            [
                "DEBUG:dakara_player.media_player.vlc:End reached callback called",
                "DEBUG:dakara_player.media_player.vlc:Will play '{}'".format(
                    Path(gettempdir()) / self.song_file_path
                ),
            ],
        )

        # assert the call
        vlc_player.callbacks["finished"].assert_not_called()
        mocked_create_thread.assert_called_with(target=vlc_player.play, args=("song",))
        mocked_is_playing_this.assert_called_with("transition")

    @patch.object(MediaPlayerVlc, "create_thread")
    @patch.object(MediaPlayerVlc, "is_playing_this")
    def test_handle_end_reached_song(
        self, mocked_is_playing_this, mocked_create_thread
    ):
        """Test song end callback after a song
        """
        # create instance
        vlc_player, _, _ = self.get_instance()
        self.set_playlist_entry(vlc_player)

        # mock the call
        vlc_player.set_callback("finished", MagicMock())
        mocked_is_playing_this.side_effect = [False, True]

        # call the method
        with self.assertLogs("dakara_player.media_player.vlc", "DEBUG"):
            vlc_player.handle_end_reached("event")

        # post assert
        self.assertIsNone(vlc_player.playlist_entry_data["song"].media)

        # assert the call
        vlc_player.callbacks["finished"].assert_called_with(42)
        mocked_create_thread.assert_not_called()
        mocked_is_playing_this.assert_has_calls([call("transition"), call("song")])

    @patch.object(MediaPlayerVlc, "create_thread")
    @patch.object(MediaPlayerVlc, "is_playing_this")
    def test_handle_end_reached_idle(
        self, mocked_is_playing_this, mocked_create_thread
    ):
        """Test song end callback after an idle screen
        """
        # create instance
        vlc_player, _, _ = self.get_instance()
        self.set_playlist_entry(vlc_player)

        # mock the call
        vlc_player.set_callback("finished", MagicMock())
        mocked_is_playing_this.side_effect = [False, False, True]

        # call the method
        with self.assertLogs("dakara_player.media_player.vlc", "DEBUG"):
            vlc_player.handle_end_reached("event")

        # assert the call
        vlc_player.callbacks["finished"].assert_not_called()
        mocked_create_thread.assert_called_with(target=vlc_player.play, args=("idle",))
        mocked_is_playing_this.assert_has_calls(
            [call("transition"), call("song"), call("idle")]
        )

    @patch.object(MediaPlayerVlc, "create_thread")
    @patch.object(MediaPlayerVlc, "is_playing_this")
    def test_handle_end_reached_invalid(
        self, mocked_is_playing_this, mocked_create_thread
    ):
        """Test song end callback on invalid state
        """
        # create instance
        vlc_player, _, _ = self.get_instance()
        self.set_playlist_entry(vlc_player)

        # mock the call
        vlc_player.set_callback("finished", MagicMock())
        mocked_is_playing_this.return_value = False

        self.assertFalse(vlc_player.stop.is_set())

        # call the method
        with self.assertLogs("dakara_player.media_player.vlc", "DEBUG"):
            vlc_player.handle_end_reached("event")

        self.assertTrue(vlc_player.stop.is_set())
        exception_class, _, _ = vlc_player.errors.get()
        self.assertIs(InvalidStateError, exception_class)

        # assert the call
        vlc_player.callbacks["finished"].assert_not_called()
        mocked_create_thread.assert_not_called()

    @patch.object(MediaPlayerVlc, "skip")
    @patch.object(MediaPlayerVlc, "is_playing_this")
    def test_handle_encountered_error(self, mocked_is_playing_this, mocked_skip):
        """Test error callback
        """
        # create instance
        vlc_player, _, _ = self.get_instance()
        self.set_playlist_entry(vlc_player)

        # mock the call
        vlc_player.set_callback("error", MagicMock())

        # call the method
        with self.assertLogs("dakara_player.media_player.vlc", "DEBUG") as logger:
            vlc_player.handle_encountered_error("event")

        # assert effect on logs
        self.assertListEqual(
            logger.output,
            [
                "DEBUG:dakara_player.media_player.vlc:Error callback called",
                "ERROR:dakara_player.media_player.vlc:Unable to play '{}'".format(
                    Path(gettempdir()) / self.song_file_path
                ),
            ],
        )

        # assert the call
        vlc_player.callbacks["error"].assert_called_with(
            42, "Unable to play current song"
        )
        mocked_skip.assert_called_with()

    @patch.object(MediaPlayerVlc, "get_timing")
    @patch.object(MediaPlayerVlc, "is_playing_this")
    def test_handle_playing_unpause(self, mocked_is_playing_this, mocked_get_timing):
        """Test playing callback when unpausing
        """
        # create instance
        vlc_player, _, _ = self.get_instance()
        self.set_playlist_entry(vlc_player)

        # mock the call
        vlc_player.set_callback("resumed", MagicMock())
        mocked_is_playing_this.return_value = True
        mocked_get_timing.return_value = 25

        # call the method
        with self.assertLogs("dakara_player.media_player.vlc", "DEBUG") as logger:
            vlc_player.handle_playing("event")

        # assert effect on logs
        self.assertListEqual(
            logger.output,
            [
                "DEBUG:dakara_player.media_player.vlc:Playing callback called",
                "DEBUG:dakara_player.media_player.vlc:Resumed play",
            ],
        )

        # assert the call
        vlc_player.callbacks["resumed"].assert_called_with(42, 25)
        mocked_is_playing_this.assert_called_with("transition")

    @patch.object(MediaPlayerVlc, "is_playing_this")
    def test_handle_playing_transition_starts(self, mocked_is_playing_this):
        """Test playing callback when transition starts
        """
        # create instance
        vlc_player, _, _ = self.get_instance()
        self.set_playlist_entry(vlc_player, started=False)

        # mock the call
        vlc_player.set_callback("started_transition", MagicMock())
        mocked_is_playing_this.side_effect = [False, False, True]

        # pre assert
        self.assertFalse(vlc_player.playlist_entry_data["transition"].started)
        self.assertFalse(vlc_player.playlist_entry_data["song"].started)

        # call the method
        with self.assertLogs("dakara_player.media_player.vlc", "DEBUG") as logger:
            vlc_player.handle_playing("event")

        # assert effect on logs
        self.assertListEqual(
            logger.output,
            [
                "DEBUG:dakara_player.media_player.vlc:Playing callback called",
                "INFO:dakara_player.media_player.vlc:Playing transition for "
                "'Song title'",
            ],
        )

        # post assert
        self.assertTrue(vlc_player.playlist_entry_data["transition"].started)
        self.assertFalse(vlc_player.playlist_entry_data["song"].started)

        # assert the call
        vlc_player.callbacks["started_transition"].assert_called_with(42)
        mocked_is_playing_this.assert_has_calls(
            [call("transition"), call("song"), call("transition")]
        )

    @patch.object(MediaPlayerVlc, "is_playing_this")
    def test_handle_playing_song(self, mocked_is_playing_this):
        """Test playing callback when song starts
        """
        # create instance
        vlc_player, _, _ = self.get_instance()
        self.set_playlist_entry(vlc_player)
        vlc_player.playlist_entry_data["song"].started = False

        # mock the call
        vlc_player.set_callback("started_song", MagicMock())
        mocked_is_playing_this.side_effect = [False, False, False, True]

        # pre assert
        self.assertFalse(vlc_player.playlist_entry_data["song"].started)

        # call the method
        with self.assertLogs("dakara_player.media_player.vlc", "DEBUG") as logger:
            vlc_player.handle_playing("event")

        # assert effect on logs
        self.assertListEqual(
            logger.output,
            [
                "DEBUG:dakara_player.media_player.vlc:Playing callback called",
                "INFO:dakara_player.media_player.vlc:Now playing 'Song title' "
                "('{}')".format(Path(gettempdir()) / self.song_file_path),
            ],
        )

        # post assert
        self.assertTrue(vlc_player.playlist_entry_data["song"].started)

        # assert the call
        vlc_player.callbacks["started_song"].assert_called_with(42)
        mocked_is_playing_this.assert_has_calls(
            [call("transition"), call("song"), call("transition"), call("song")]
        )

    @patch.object(MediaPlayerVlc, "is_playing_this")
    def test_handle_playing_media_starts_track_id(self, mocked_is_playing_this):
        """Test playing callback when media starts with requested track ID
        """
        # create instance
        vlc_player, (mocked_instance, _, _), _ = self.get_instance()
        mocked_player = mocked_instance.media_player_new.return_value
        self.set_playlist_entry(vlc_player)
        vlc_player.playlist_entry_data["song"].audio_track_id = 99

        # mock the call
        vlc_player.set_callback("started_song", MagicMock())
        mocked_is_playing_this.side_effect = [False, False, False, True]

        # call the method
        with self.assertLogs("dakara_player.media_player.vlc", "DEBUG") as logger:
            vlc_player.handle_playing("event")

        # assert effect on logs
        self.assertListEqual(
            logger.output,
            [
                "DEBUG:dakara_player.media_player.vlc:Playing callback called",
                "DEBUG:dakara_player.media_player.vlc:Requesting to play audio "
                "track 99",
                "INFO:dakara_player.media_player.vlc:Now playing 'Song title' "
                "('{}')".format(Path(gettempdir()) / self.song_file_path),
            ],
        )

        # assert the call
        vlc_player.callbacks["started_song"].assert_called_with(42)
        mocked_player.audio_set_track.assert_called_with(99)
        mocked_is_playing_this.assert_has_calls(
            [call("transition"), call("song"), call("transition"), call("song")]
        )

    @patch.object(MediaPlayerVlc, "is_playing_this")
    def test_handle_playing_idle_starts(self, mocked_is_playing_this):
        """Test playing callback when idle screen starts
        """
        # create instance
        vlc_player, _, _ = self.get_instance()

        # mock the call
        mocked_is_playing_this.side_effect = [False, False, False, False, True]

        # call the method
        with self.assertLogs("dakara_player.media_player.vlc", "DEBUG") as logger:
            vlc_player.handle_playing("event")

        # assert effect on logs
        self.assertListEqual(
            logger.output,
            [
                "DEBUG:dakara_player.media_player.vlc:Playing callback called",
                "DEBUG:dakara_player.media_player.vlc:Playing idle screen",
            ],
        )

        # assert the call
        mocked_is_playing_this.assert_has_calls(
            [
                call("transition"),
                call("song"),
                call("transition"),
                call("song"),
                call("idle"),
            ]
        )

    @patch.object(MediaPlayerVlc, "is_playing_this")
    def test_handle_playing_invalid(self, mocked_is_playing_this):
        """Test playing callback on invalid state
        """
        # create instance
        vlc_player, _, _ = self.get_instance()

        # setup mock
        mocked_is_playing_this.return_value = False

        self.assertFalse(vlc_player.stop.is_set())

        # call the method
        with self.assertLogs("dakara_player.media_player.vlc", "DEBUG"):
            vlc_player.handle_playing("event")

        self.assertTrue(vlc_player.stop.is_set())
        exception_class, _, _ = vlc_player.errors.get()
        self.assertIs(InvalidStateError, exception_class)

    @patch.object(MediaPlayerVlc, "get_timing")
    def test_handle_paused(self, mocked_get_timing):
        """Test paused callback
        """
        # create instance
        vlc_player, _, _ = self.get_instance()
        self.set_playlist_entry(vlc_player)

        # mock the call
        vlc_player.set_callback("paused", MagicMock())
        mocked_get_timing.return_value = 25

        # call the method
        with self.assertLogs("dakara_player.media_player.vlc", "DEBUG") as logger:
            vlc_player.handle_paused("event")

        # assert effect on logs
        self.assertListEqual(
            logger.output,
            [
                "DEBUG:dakara_player.media_player.vlc:Paused callback called",
                "DEBUG:dakara_player.media_player.vlc:Paused",
            ],
        )

        # assert the call
        vlc_player.callbacks["paused"].assert_called_with(42, 25)

    def test_default_backgrounds(self):
        """Test to instanciate with default backgrounds
        """
        # create object
        _, _, (_, mocked_background_loader_class, _) = self.get_instance()

        # assert the instanciation of the background loader
        mocked_background_loader_class.assert_called_with(
            directory=Path(""),
            default_directory=Path("bg"),
            background_filenames={"transition": None, "idle": None},
            default_background_filenames={
                "transition": "transition.png",
                "idle": "idle.png",
            },
        )

    def test_custom_backgrounds(self):
        """Test to instanciate with an existing backgrounds directory
        """
        # create object
        _, _, (_, mocked_background_loader_class, _) = self.get_instance(
            {
                "backgrounds": {
                    "directory": Path("custom") / "bg",
                    "transition_background_name": "custom_transition.png",
                    "idle_background_name": "custom_idle.png",
                }
            }
        )

        # assert the instanciation of the background loader
        mocked_background_loader_class.assert_called_with(
            directory=Path("custom") / "bg",
            default_directory=Path("bg"),
            background_filenames={
                "transition": "custom_transition.png",
                "idle": "custom_idle.png",
            },
            default_background_filenames={
                "transition": "transition.png",
                "idle": "idle.png",
            },
        )

    def test_default_durations(self):
        """Test to instanciate with default durations
        """
        # create object
        vlc_player, _, _ = self.get_instance()

        # assert the instance
        self.assertDictEqual(vlc_player.durations, {"transition": 10, "idle": 20})

    def test_custom_durations(self):
        """Test to instanciate with custom durations
        """
        # create object
        vlc_player, _, _ = self.get_instance({"durations": {"transition_duration": 5}})

        # assert the instance
        self.assertDictEqual(vlc_player.durations, {"transition": 5, "idle": 20})

    @patch("dakara_player.media_player.base.PLAYER_CLOSING_DURATION", 0)
    @patch.object(MediaPlayerVlc, "stop_player")
    def test_slow_close(self, mocked_stop_player):
        """Test to close VLC when it takes a lot of time
        """
        vlc_player, _, _ = self.get_instance()
        mocked_stop_player.side_effect = lambda: sleep(1)

        with self.assertLogs("dakara_player.media_player.base", "DEBUG") as logger:
            vlc_player.exit_worker()

        self.assertListEqual(
            logger.output,
            ["WARNING:dakara_player.media_player.base:VLC takes too long to stop"],
        )

    @patch.object(TextGenerator, "create_transition_text")
    @patch.object(TextGenerator, "create_idle_text")
    def test_generate_text_invalid(
        self, mocked_create_idle_text, mocked_create_transition_text
    ):
        """Test to generate invalid text screen
        """
        vlc_player, _, _ = self.get_instance()

        with self.assertRaisesRegex(
            ValueError, "Unexpected action to generate text to: none"
        ):
            vlc_player.generate_text("none")

        mocked_create_idle_text.assert_not_called()
        mocked_create_transition_text.assert_not_called()

    def test_play_invalid(self):
        """Test to play invalid action
        """
        vlc_player, _, _ = self.get_instance()

        with self.assertRaisesRegex(ValueError, "Unexpected action to play: none"):
            vlc_player.play("none")

        vlc_player.player.play.assert_not_called()
