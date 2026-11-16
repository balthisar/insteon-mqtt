#===========================================================================
#
# Tests for: insteont_mqtt/device/Dimmer.py
#
#===========================================================================
import pytest
import time
# from pprint import pprint
from unittest import mock
from unittest.mock import call
import insteon_mqtt as IM
import insteon_mqtt.device.Dimmer as Dimmer
import insteon_mqtt.message as Msg
import insteon_mqtt.util as util
import helpers as H

@pytest.fixture
def test_device(tmpdir):
    '''
    Returns a generically configured dimmer for testing
    '''
    protocol = H.main.MockProtocol()
    modem = H.main.MockModem(tmpdir)
    addr = IM.Address(0x01, 0x02, 0x03)
    device = Dimmer(protocol, modem, addr)
    return device


class Test_Scene_Function():
    @pytest.mark.parametrize("is_on,group,level", [
        (True, 0x01, None),
        (True, None, None),
        (True, 0x01, 128),
        (False, None, None),
    ])
    def test_scene(self, test_device, is_on, group, level):
        test_device.scene(is_on, group=group, level=level)

        # test the message contents
        if group is None:
            group = 0x01
        assert len(test_device.protocol.sent) == 1
        assert test_device.protocol.sent[0].msg.cmd1 == 0x30
        assert test_device.protocol.sent[0].msg.cmd2 == 0x00
        assert test_device.protocol.sent[0].msg.data[0] == group # group
        if level is None:
            # don't use_on_level
            if is_on:
                assert test_device.protocol.sent[0].msg.data[1] == 0x00
            else:
                # off always 0x01
                assert test_device.protocol.sent[0].msg.data[1] == 0x01
            assert test_device.protocol.sent[0].msg.data[2] == 0x00
        else:
            # use_on_level
            assert test_device.protocol.sent[0].msg.data[1] == 0x01
            assert test_device.protocol.sent[0].msg.data[2] == int(level)
        if is_on:
            assert test_device.protocol.sent[0].msg.data[3] == 0x11
        else:
            assert test_device.protocol.sent[0].msg.data[3] == 0x13
        assert test_device.protocol.sent[0].msg.data[4] == 0x01
        assert test_device.protocol.sent[0].msg.data[5] == 0x00

        # Test the broadcast_scene_level contents
        test_device.protocol.sent[0].handler.on_done(True, None, None)
        scene_timestamp = test_device.broadcast_scene_level['timestamp'] + 1
        scene_level = test_device.broadcast_scene_level['level']
        if not is_on:
            assert scene_timestamp == 1
        elif level is None:
            assert scene_timestamp == 1
            assert scene_level == 0
        else:
            assert scene_timestamp >= time.time()
            assert scene_level == level


class Test_Scene_Set_Function():
    """Tests for the Scene.scene_set command."""

    def make_scene(self, tmpdir, resp_cls=None, resp_data=None,
                   add_resp_entry=True):
        """Create a main dimmer and a responder linked for group 1."""
        protocol = H.main.MockProtocol()
        modem = H.main.MockModem(tmpdir)
        main = Dimmer(protocol, modem, IM.Address(0x01, 0x02, 0x03), "main")

        resp_cls = resp_cls if resp_cls else IM.device.Dimmer
        resp = resp_cls(protocol, modem, IM.Address(0x04, 0x05, 0x06),
                        "resp")
        modem.add(main)
        modem.add(resp)

        # Main is the controller of the responder for group 1.
        flags = Msg.DbFlags(in_use=True, is_controller=True,
                            is_last_rec=False)
        entry = IM.db.DeviceEntry(resp.addr, 0x01, 0x0ff7, flags,
                                  bytes([0x03, 0x00, 0x01]))
        main.db.add_entry(entry, save=False)

        # Responder entry on the linked device unless asked to skip it.
        if add_resp_entry:
            flags = Msg.DbFlags(in_use=True, is_controller=False,
                                is_last_rec=False)
            data = bytes(resp_data if resp_data else [0xff, 0x1f, 0x01])
            entry = IM.db.DeviceEntry(main.addr, 0x01, 0x0ff7, flags, data)
            resp.db.add_entry(entry, save=False)

        return protocol, modem, main, resp

    def test_dimmer(self, tmpdir):
        protocol, modem, main, resp = self.make_scene(tmpdir)
        with mock.patch.object(IM.CommandSeq, 'add') as mocked:
            main.scene_set(level=128)
            calls = [
                call(main.set, is_on=True, group=0x01, level=128,
                     reason='device'),
                call(resp.set, is_on=True, group=0x01, level=128,
                     reason='scene'),
            ]
            mocked.assert_has_calls(calls)
            assert mocked.call_count == 2

    def test_off(self, tmpdir):
        protocol, modem, main, resp = self.make_scene(tmpdir)
        with mock.patch.object(IM.CommandSeq, 'add') as mocked:
            main.scene_set(level=0)
            calls = [
                call(main.set, is_on=False, group=0x01, level=0,
                     reason='device'),
                call(resp.set, is_on=False, group=0x01, level=0,
                     reason='scene'),
            ]
            mocked.assert_has_calls(calls)
            assert mocked.call_count == 2

    def test_reason(self, tmpdir):
        protocol, modem, main, resp = self.make_scene(tmpdir)
        with mock.patch.object(IM.CommandSeq, 'add') as mocked:
            main.scene_set(level=128, reason="automation")
            calls = [
                call(main.set, is_on=True, group=0x01, level=128,
                     reason='automation'),
                call(resp.set, is_on=True, group=0x01, level=128,
                     reason='scene'),
            ]
            mocked.assert_has_calls(calls)

    def test_switch(self, tmpdir):
        protocol, modem, main, resp = self.make_scene(
            tmpdir, resp_cls=IM.device.Switch)
        with mock.patch.object(IM.CommandSeq, 'add') as mocked:
            main.scene_set(level=128)
            calls = [
                call(main.set, is_on=True, group=0x01, level=128,
                     reason='device'),
                call(resp.set, is_on=True, group=0x01, level=None,
                     reason='scene'),
            ]
            mocked.assert_has_calls(calls)

    def test_keypad_local_group(self, tmpdir):
        # The KeypadLinc responder entry says button 3 is the local group.
        protocol, modem, main, resp = self.make_scene(
            tmpdir, resp_cls=IM.device.KeypadLincDimmer,
            resp_data=[0xff, 0x1f, 0x03])
        with mock.patch.object(IM.CommandSeq, 'add') as mocked:
            main.scene_set(level=128)
            mocked.assert_has_calls([
                call(resp.set, is_on=True, group=0x03, level=128,
                     reason='scene'),
            ])

    def test_invalid_level(self, tmpdir):
        protocol, modem, main, resp = self.make_scene(tmpdir)
        result = []
        with mock.patch.object(IM.CommandSeq, 'add') as mocked:
            main.scene_set(level=300,
                           on_done=lambda s, m, d: result.append(s))
            mocked.assert_not_called()
        assert result == [False]

    def test_missing_responder_entry(self, tmpdir, caplog):
        protocol, modem, main, resp = self.make_scene(
            tmpdir, add_resp_entry=False)
        with mock.patch.object(IM.CommandSeq, 'add') as mocked:
            main.scene_set(level=128)
            assert mocked.call_count == 1
            mocked.assert_called_once_with(main.set, is_on=True, group=0x01,
                                           level=128, reason='device')
        assert 'has no responder entry' in caplog.text

    def test_unknown_device(self, tmpdir):
        protocol = H.main.MockProtocol()
        modem = H.main.MockModem(tmpdir)
        main = Dimmer(protocol, modem, IM.Address(0x01, 0x02, 0x03), "main")
        modem.add(main)

        # Controller entry for a device that isn't in the config.
        flags = Msg.DbFlags(in_use=True, is_controller=True,
                            is_last_rec=False)
        entry = IM.db.DeviceEntry(IM.Address(0x09, 0x09, 0x09), 0x01, 0x0ff7,
                                  flags, bytes([0x03, 0x00, 0x01]))
        main.db.add_entry(entry, save=False)

        with mock.patch.object(IM.CommandSeq, 'add') as mocked:
            main.scene_set(level=128)
            assert mocked.call_count == 1

    def test_battery_device_skipped(self, tmpdir):
        protocol, modem, main, resp = self.make_scene(
            tmpdir, resp_cls=IM.device.BatterySensor)
        with mock.patch.object(IM.CommandSeq, 'add') as mocked:
            main.scene_set(level=128)
            assert mocked.call_count == 1

    def test_sequence(self, tmpdir):
        protocol, modem, main, resp = self.make_scene(tmpdir)
        result = []
        main.scene_set(level=128, on_done=lambda s, m, d: result.append(s))

        # The main device is set first, then the responder.
        assert len(protocol.sent) == 1
        assert protocol.sent[0].msg.to_addr == main.addr
        protocol.sent[0].handler.on_done(True, "", None)

        assert len(protocol.sent) == 2
        assert protocol.sent[1].msg.to_addr == resp.addr
        protocol.sent[1].handler.on_done(True, "", None)

        assert result == [True]
