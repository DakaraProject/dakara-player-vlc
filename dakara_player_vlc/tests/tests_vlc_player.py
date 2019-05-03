from unittest import TestCase
from unittest.mock import MagicMock, NonCallableMagicMock, patch, ANY
from threading import Event
from queue import Queue

from vlc import State, EventType
from path import Path

from dakara_player_vlc.version import __version__ as dakara_player_vlc_version
from dakara_player_vlc.vlc_player import (
    VlcPlayer,
    mrl_to_path,
    IDLE_BG_NAME,
    TRANSITION_BG_NAME,
)

from dakara_player_vlc.resources_manager import (
    get_test_material,
    get_background,
    PATH_TEST_MATERIALS,
)


class VlcPlayerTestCase(TestCase):
    """Test the VLC player module
    """

    def setUp(self):
        # create instance parameter
        self.instance_parameters = []

        # create fullscreen flag
        self.fullscreen = True

        # create kara folder
        self.kara_folder = PATH_TEST_MATERIALS

        # create media parameter
        self.media_parameters = ["no-video"]

        # create idle background path
        self.idle_background_path = get_background(IDLE_BG_NAME)

        # create transition background path
        self.transition_background_path = get_background(TRANSITION_BG_NAME)

        # create transition duration
        self.transition_duration = 1

        # create a mock text generator
        self.text_generator = MagicMock()

        # create a subtitle
        self.subtitle_path = get_test_material("song.ass")

        # create song path
        self.song_file_path = get_test_material("song.png")

        # create playlist entry
        self.playlist_entry = {"id": 42, "song": {"file_path": self.song_file_path}}

        # create vlc player
        self.vlc_player = VlcPlayer(
            Event(),
            Queue(),
            {
                "kara_folder": self.kara_folder,
                "fullscreen": self.fullscreen,
                "transition_duration": self.transition_duration,
                "vlc": {
                    "instance_parameters": self.instance_parameters,
                    "media_parameters": self.media_parameters,
                },
            },
            self.text_generator,
        )

        # set callbacks for VLC
        self.vlc_player.set_finished_callback(lambda self: None)
        self.vlc_player.set_error_callback(lambda self, message: None)

    @staticmethod
    def get_event_and_callback():
        """Get an event and a callback that sets this event
        """
        event = Event()

        def callback(*args, **kwargs):
            """Callback that sets the joined event
            """
            event.set()

        return event, callback

    def test_set_callbacks(self):
        """Test the assignation of callbacks
        """
        names = (
            "started_transition",
            "started_song",
            "could_not_play",
            "finished",
            "paused",
            "resumed",
            "error",
        )

        for name in names:
            method_name = "set_{}_callback".format(name)
            callback_name = "{}_callback".format(name)

            method = getattr(self.vlc_player, method_name)
            callback = MagicMock()

            # pre assert
            self.assertIsNot(getattr(self.vlc_player, callback_name), callback)

            # call the method
            method(callback)

            # post assert
            self.assertIs(getattr(self.vlc_player, callback_name), callback)

    def test_play_idle_screen(self):
        """Test the display of the idle screen
        """
        # mock the text generator
        self.text_generator.create_idle_text.return_value = self.subtitle_path

        # create an event for when the player starts to play
        is_playing, callback_is_playing = self.get_event_and_callback()
        self.vlc_player.event_manager.event_attach(
            EventType.MediaPlayerPlaying, callback_is_playing
        )

        # pre assertions
        self.assertIsNone(self.vlc_player.player.get_media())
        self.assertEqual(self.vlc_player.player.get_state(), State.NothingSpecial)

        # call the method
        self.vlc_player.play_idle_screen()

        # wait for the player to start actually playing
        is_playing.wait()

        # call assertions
        self.text_generator.create_idle_text.assert_called_once_with(
            {
                "notes": [
                    "VLC " + self.vlc_player.vlc_version,
                    "Dakara player " + dakara_player_vlc_version,
                ]
            }
        )

        # post assertions
        self.assertEqual(self.vlc_player.player.get_state(), State.Playing)

        self.assertIsNotNone(self.vlc_player.player.get_media())
        media = self.vlc_player.player.get_media()
        file_path = mrl_to_path(media.get_mrl())
        self.assertEqual(file_path, self.idle_background_path)
        # TODO check which subtitle file is read
        # seems impossible to do for now

    def test_play_playlist_entry(self):
        """Test to play a playlist entry

        First, the transition screen is played, then the song itself.
        """
        # mock the text generator
        self.text_generator.create_transition_text.return_value = self.subtitle_path

        # mock the callbacks
        self.vlc_player.started_transition_callback = MagicMock()
        self.vlc_player.started_song_callback = MagicMock()

        # create an event for when the player starts to play
        is_playing, callback_is_playing = self.get_event_and_callback()
        self.vlc_player.event_manager.event_attach(
            EventType.MediaPlayerPlaying, callback_is_playing
        )

        # pre assertions
        self.assertIsNone(self.vlc_player.playing_id)
        self.assertFalse(self.vlc_player.in_transition)
        self.assertIsNone(self.vlc_player.player.get_media())
        self.assertEqual(self.vlc_player.player.get_state(), State.NothingSpecial)

        # call the method
        self.vlc_player.play_playlist_entry(self.playlist_entry)

        # wait for the player to start actually playing the transition
        is_playing.wait()

        # reset the event to catch when the player starts to play the song
        is_playing.clear()

        # call assertions
        self.text_generator.create_transition_text.assert_called_once_with(
            self.playlist_entry
        )

        # post assertions for transition screen
        self.assertIsNotNone(self.vlc_player.playing_id)
        self.assertTrue(self.vlc_player.in_transition)
        self.assertEqual(self.vlc_player.player.get_state(), State.Playing)

        self.assertIsNotNone(self.vlc_player.player.get_media())
        media = self.vlc_player.player.get_media()
        file_path = mrl_to_path(media.get_mrl())
        self.assertEqual(file_path, self.transition_background_path)
        # TODO check which subtitle file is read
        # seems impossible to do for now

        # assert the started transition callback has been called
        self.vlc_player.started_transition_callback.assert_called_with(
            self.playlist_entry["id"]
        )

        # wait for the player to start actually playing the song
        is_playing.wait()

        # post assertions for song
        self.assertFalse(self.vlc_player.in_transition)
        self.assertEqual(self.vlc_player.player.get_state(), State.Playing)

        self.assertIsNotNone(self.vlc_player.player.get_media())
        media = self.vlc_player.player.get_media()
        file_path = mrl_to_path(media.get_mrl())
        self.assertEqual(file_path, self.song_file_path)

        # assert the started song callback has been called
        self.vlc_player.started_song_callback.assert_called_with(
            self.playlist_entry["id"]
        )

        # close the player
        self.vlc_player.stop_player()

    @patch("dakara_player_vlc.vlc_player.os.path.isfile")
    def test_play_playlist_entry_error_file(self, mock_isfile):
        """Test to play a file that does not exist
        """
        # mock the system call
        mock_isfile.return_value = False

        # mock the callbacks
        self.vlc_player.started_transition_callback = MagicMock()
        self.vlc_player.started_song_callback = MagicMock()
        self.vlc_player.could_not_play_callback = MagicMock()

        # pre assertions
        self.assertIsNone(self.vlc_player.playing_id)
        self.assertIsNone(self.vlc_player.player.get_media())
        self.assertEqual(self.vlc_player.player.get_state(), State.NothingSpecial)

        # call the method
        self.vlc_player.play_playlist_entry(self.playlist_entry)

        # call assertions
        mock_isfile.assert_called_once_with(self.song_file_path)

        # post assertions
        self.assertIsNone(self.vlc_player.playing_id)
        self.assertIsNone(self.vlc_player.player.get_media())
        self.assertEqual(self.vlc_player.player.get_state(), State.NothingSpecial)

        # assert the callbacks
        self.vlc_player.started_transition_callback.assert_not_called()
        self.vlc_player.started_song_callback.assert_not_called()
        self.vlc_player.could_not_play_callback.assert_called_with(
            self.playlist_entry["id"]
        )

    def test_end_reached_callback_transition(self):
        """Test song end callback for after a transition screen
        """
        # mock the call
        self.vlc_player.in_transition = NonCallableMagicMock()
        in_transition = self.vlc_player.in_transition
        self.vlc_player.create_thread = MagicMock()
        self.vlc_player.media_pending = MagicMock()
        self.vlc_player.media_pending.get_mrl.return_value = "file:///test.mkv"
        self.vlc_player.started_song_callback = MagicMock()
        self.vlc_player.playing_id = 999

        # call the method
        self.vlc_player.end_reached_callback("event")

        # assert the call
        in_transition.__bool__.assert_called_with()
        self.assertFalse(self.vlc_player.in_transition)
        self.vlc_player.create_thread.assert_called_with(
            target=self.vlc_player.play_media, args=(self.vlc_player.media_pending,)
        )
        self.vlc_player.media_pending.get_mrl.assert_called_with()
        self.vlc_player.create_thread.return_value.start.assert_called_with()
        self.vlc_player.started_song_callback.assert_called_with(999)

    def test_end_reached_callback_idle(self):
        """Test song end callback for after an idle screen
        """
        # mock the call
        self.vlc_player.in_transition = NonCallableMagicMock()
        in_transition = self.vlc_player.in_transition
        in_transition.__bool__.return_value = False
        self.vlc_player.is_idle = MagicMock()
        self.vlc_player.is_idle.return_value = True
        self.vlc_player.create_thread = MagicMock()

        # call the method
        self.vlc_player.end_reached_callback("event")

        # assert the call
        in_transition.__bool__.assert_called_with()
        self.vlc_player.is_idle.assert_called_with()
        self.vlc_player.create_thread.assert_called_with(
            target=self.vlc_player.play_idle_screen
        )
        self.vlc_player.create_thread.return_value.start.assert_called_with()

    def test_end_reached_callback_finished(self):
        """Test song end callback for after an actual song
        """
        # mock the call
        self.vlc_player.in_transition = NonCallableMagicMock()
        in_transition = self.vlc_player.in_transition
        in_transition.__bool__.return_value = False
        self.vlc_player.is_idle = MagicMock()
        self.vlc_player.is_idle.return_value = False
        self.vlc_player.playing_id = MagicMock()
        self.vlc_player.finished_callback = MagicMock()
        self.vlc_player.playing_id = 999

        # call the method
        self.vlc_player.end_reached_callback("event")

        # assert the call
        in_transition.__bool__.assert_called_with()
        self.vlc_player.is_idle.assert_called_with()
        self.vlc_player.finished_callback.assert_called_with(999)

    @patch("dakara_player_vlc.vlc_player.vlc.libvlc_errmsg")
    def test_encountered_error_callback(self, mock_libvcl_errmsg):
        """Test error callback
        """
        # mock the call
        mock_libvcl_errmsg.return_value = b"error"
        self.vlc_player.error_callback = MagicMock()
        self.vlc_player.playing_id = NonCallableMagicMock()
        self.vlc_player.playing_id = 999

        # call the method
        self.vlc_player.encountered_error_callback("event")

        # assert the call
        mock_libvcl_errmsg.assert_called_with()
        self.vlc_player.error_callback.assert_called_with(999, "error")
        self.assertIsNone(self.vlc_player.playing_id)
        self.assertFalse(self.vlc_player.in_transition)

    def test_set_pause(self):
        """Test to pause and unpause the player
        """
        # mock the text generator
        self.text_generator.create_transition_text.return_value = self.subtitle_path

        # mock the callbacks
        self.vlc_player.paused_callback = MagicMock()
        self.vlc_player.resumed_callback = MagicMock()

        # create an event for when the player starts to play
        is_playing, callback_is_playing = self.get_event_and_callback()
        self.vlc_player.event_manager.event_attach(
            EventType.MediaPlayerPlaying, callback_is_playing
        )

        # start the playlist entry
        self.vlc_player.play_playlist_entry(self.playlist_entry)

        # wait for the player to start actually playing the song
        is_playing.wait()
        is_playing.clear()
        is_playing.wait()

        # pre asserts
        self.assertFalse(self.vlc_player.is_paused())
        self.assertFalse(self.vlc_player.is_idle())

        # call the method to pause the player
        self.vlc_player.set_pause(True)
        timing = self.vlc_player.get_timing()

        # assert the call
        self.assertTrue(self.vlc_player.is_paused())

        # assert the callback
        self.vlc_player.paused_callback.assert_called_with(
            self.playlist_entry["id"], timing
        )
        self.vlc_player.resumed_callback.assert_not_called()

        # reset the mocks
        self.vlc_player.paused_callback.reset_mock()
        self.vlc_player.resumed_callback.reset_mock()

        # call the method to resume the player
        self.vlc_player.set_pause(False)

        # assert the call
        self.assertFalse(self.vlc_player.is_paused())

        # assert the callback
        self.vlc_player.paused_callback.assert_not_called()
        self.vlc_player.resumed_callback.assert_called_with(
            self.playlist_entry["id"],
            timing,  # on a slow computer, the timing may be inaccurate
        )

        # close the player
        self.vlc_player.stop_player()

    def test_set_double_pause(self):
        """Test that double pause and double resume have no effects
        """
        # mock the text generator
        self.text_generator.create_transition_text.return_value = self.subtitle_path

        # mock the callbacks
        self.vlc_player.paused_callback = MagicMock()
        self.vlc_player.resumed_callback = MagicMock()

        # create an event for when the player starts to play
        is_playing, callback_is_playing = self.get_event_and_callback()
        self.vlc_player.event_manager.event_attach(
            EventType.MediaPlayerPlaying, callback_is_playing
        )

        # start the playlist entry
        self.vlc_player.play_playlist_entry(self.playlist_entry)

        # wait for the player to start actually playing the song
        is_playing.wait()
        is_playing.clear()
        is_playing.wait()

        # pre asserts
        self.assertFalse(self.vlc_player.is_paused())
        self.assertFalse(self.vlc_player.is_idle())

        # call the method to pause the player
        self.vlc_player.set_pause(True)

        # assert the call
        self.assertTrue(self.vlc_player.is_paused())

        # assert the callback
        self.vlc_player.paused_callback.assert_called_with(ANY, ANY)
        self.vlc_player.resumed_callback.assert_not_called()

        # reset the mocks
        self.vlc_player.paused_callback.reset_mock()
        self.vlc_player.resumed_callback.reset_mock()

        # re-call the method to pause the player
        self.vlc_player.set_pause(True)

        # assert the call
        self.assertTrue(self.vlc_player.is_paused())

        # assert the callback
        self.vlc_player.paused_callback.assert_not_called()
        self.vlc_player.resumed_callback.assert_not_called()

        # reset the mocks
        self.vlc_player.paused_callback.reset_mock()
        self.vlc_player.resumed_callback.reset_mock()

        # call the method to resume the player
        self.vlc_player.set_pause(False)

        # assert the call
        self.assertFalse(self.vlc_player.is_paused())

        # assert the callback
        self.vlc_player.paused_callback.assert_not_called()
        self.vlc_player.resumed_callback.assert_called_with(ANY, ANY)

        # reset the mocks
        self.vlc_player.paused_callback.reset_mock()
        self.vlc_player.resumed_callback.reset_mock()

        # re-call the method to resume the player
        self.vlc_player.set_pause(False)

        # assert the call
        self.assertFalse(self.vlc_player.is_paused())

        # assert the callback
        self.vlc_player.paused_callback.assert_not_called()
        self.vlc_player.resumed_callback.assert_not_called()

        # close the player
        self.vlc_player.stop_player()


class VlcPlayerCustomTestCase(TestCase):
    """Test the VLC player class with custom resources
    """

    def setUp(self):
        # create a mock text generator
        self.text_generator = MagicMock()

        # create stop event
        self.stop = Event()

        # create errors queue
        self.errors = Queue()

    def test_default(self):
        """Test to instanciate with default parameters

        In that case, backgrounds come from the fallback directory.
        """
        # create object
        vlc_player = VlcPlayer(self.stop, self.errors, {}, self.text_generator)

        # assert the object
        self.assertEqual(vlc_player.idle_bg_path, get_background(IDLE_BG_NAME))
        self.assertEqual(
            vlc_player.transition_bg_path, get_background(TRANSITION_BG_NAME)
        )

    def test_custom_background_directory_success(self):
        """Test to instanciate with an existing backgrounds directory

        In that case, backgrounds come from this directory.
        """
        # create object
        vlc_player = VlcPlayer(
            self.stop,
            self.errors,
            {"backgrounds": {"directory": PATH_TEST_MATERIALS}},
            self.text_generator,
        )

        # assert the object
        self.assertEqual(vlc_player.idle_bg_path, get_test_material(IDLE_BG_NAME))
        self.assertEqual(
            vlc_player.transition_bg_path, get_test_material(TRANSITION_BG_NAME)
        )

    def test_custom_background_directory_fail(self):
        """Test to instanciate with an inexisting backgrounds directory

        In that case, backgrounds come from the fallback directory.
        """
        # create object
        vlc_player = VlcPlayer(
            self.stop,
            self.errors,
            {"backgrounds": {"directory": "nowhere"}},
            self.text_generator,
        )

        # assert the object
        self.assertEqual(vlc_player.idle_bg_path, get_background(IDLE_BG_NAME))
        self.assertEqual(
            vlc_player.transition_bg_path, get_background(TRANSITION_BG_NAME)
        )

    def test_custom_background_names_success(self):
        """Test to instanciate with existing background names

        In that case, backgrounds come from the custom directory and have the
        correct name.
        """
        # create object
        vlc_player = VlcPlayer(
            self.stop,
            self.errors,
            {
                "backgrounds": {
                    "directory": PATH_TEST_MATERIALS,
                    "idle_background_name": "song.png",
                    "transition_background_name": "song.png",
                }
            },
            self.text_generator,
        )

        # assert the object
        self.assertEqual(vlc_player.idle_bg_path, get_test_material("song.png"))
        self.assertEqual(vlc_player.transition_bg_path, get_test_material("song.png"))

    def test_custom_background_names_fail(self):
        """Test to instanciate with background names that do not exist

        In that case, backgrounds come from the custom directory and have the
        default name.
        """
        # create object
        vlc_player = VlcPlayer(
            self.stop,
            self.errors,
            {
                "backgrounds": {
                    "directory": PATH_TEST_MATERIALS,
                    "idle_background_name": "nothing",
                    "transition_background_name": "nothing",
                }
            },
            self.text_generator,
        )

        # assert the object
        self.assertEqual(vlc_player.idle_bg_path, get_test_material(IDLE_BG_NAME))
        self.assertEqual(
            vlc_player.transition_bg_path, get_test_material(TRANSITION_BG_NAME)
        )


class MrlToPathTestCase(TestCase):
    """Test the `mrl_to_path` function
    """

    def test(self):
        """Test to call the function with various arguments
        """
        self.assertEqual(mrl_to_path("file:///a/b/c"), Path("/a/b/c").normpath())
        self.assertEqual(mrl_to_path("file:///a/b%20b/c"), Path("/a/b b/c").normpath())
        self.assertEqual(mrl_to_path("file:///C:/a/b"), Path("C:/a/b").normpath())