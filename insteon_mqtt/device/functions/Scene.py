#===========================================================================
#
# Scene Functions.  This enables the simulated scene function used on the
# Dimmer, Switch, and KeypadLinc.
#
#===========================================================================
import time
from ..base import Base
from ... import handler
from ... import log
from ... import message as Msg
from ... import on_off
from ... import util
from ...CommandSeq import CommandSeq

LOG = log.get_logger()


class Scene(Base):
    """Scene Trait Abstract Class

    This is an abstract class that provides support for the Scene topic.
    """
    def __init__(self, protocol, modem, address, name=None, config_extra=None):
        """Constructor

        Args:
          protocol (Protocol): The Protocol object used to communicate
                   with the Insteon network.  This is needed to allow the
                   device to send messages to the PLM modem.
          modem (Modem): The Insteon modem used to find other devices.
          address (Address): The address of the device.
          name (str): Nice alias name to use for the device.
          config_extra (dict): Extra configuration settings
        """
        super().__init__(protocol, modem, address, name, config_extra)

        self.cmd_map.update({
            'scene' : self.scene,
            'scene_set' : self.scene_set,
            })

        # NOTE!
        # The class extending this class needs to define the controller groups
        # in the self.group_map.  Only these groups will be valid scene
        # targets

    def scene(self, is_on, group=0x01, name=None, reason=None, level=None,
              on_done=None):
        """Trigger a scene on the device.

        Triggering a scene is the same as simulating a button press on the
        device.  It will change the state of the device and notify responders
        that are linked to the device to update their state as well.

        The process looks like:
          Modem -> Device OutExtended Scene Command
          Modem <- Device Device ACK
          World <- Device Broadcast
          Modem <- Device Direct Cleanup
          Modem <- Device Cleanup Report [Not always received]

        Args:
          is_on (bool): True for an on command, False for an off command.
          group (int): The group on the device to simulate.  For this device,
                this must be 1.
          name (None): Not used, only here for modem compatibility.
          reason (str):  This is optional and is used to identify why the
                 command was sent. It is passed through to the output signal
                 when the state changes - nothing else is done with it.
          level (int):  Brightness level [0-255] to set the device to if
                 the device supports dimming.  Linked devices will always
                 turn on to the level specified in their link.
          on_done: Finished callback.  This is called when the command has
                   completed.  Signature is: on_done(success, msg, data)
        """
        LOG.info("Device %s scene %s", self.addr, "on" if is_on else "off")
        if group is None:
            group = 0x01
        assert group in self.group_map

        if name is not None:
            LOG.error("Device %s scenes cannot use names, groups only.",
                      self.addr)

        on_done = util.make_callback(on_done)

        # Send an 0x30 all link command to simulate the button being pressed
        # on the switch.  See page 163 of insteon dev guide
        cmd1 = 0x11 if is_on else 0x13
        # Must specify use_on_level if cmd is off
        use_on_level = 0x00 if is_on else 0x01
        # Only used if use_on_level is 0x01, default 0x00 for Off cmd
        on_level = 0x00
        if level is not None:
            use_on_level = 0x01
            on_level = int(level)
        data = bytes([
            group,         # D1 = group (button)
            use_on_level,  # D2 = 0x00 use level in scene db or 0x01 use D3
            on_level,      # D3 = on level if D2=0x01
            cmd1,          # D4 = cmd1 to send to linked devices
            0x01,          # D5 = cmd2 to send
            0x00,          # D6 = use ramp rate in scene db or 0x01 for fast
            ] + [0x00] * 8)
        msg = Msg.OutExtended.direct(self.addr, 0x30, 0x00, data)

        # Use the standard command handler which will notify us when the
        # command is ACK'ed.
        def our_on_done(success, msg, data):
            if success:
                # We know that a broadcast command is going to arrive from the
                # device.  The amount of time we need to wait is unknown, but
                # this is a reasonable guess, that will be overwritten by the
                # wait time calculated by the arriving broadcast message.
                self.protocol.set_wait_time(time.time() + 1)

                # If level was specified, then Save the level value so it can
                # be used when the broadcast message is received.
                if is_on and use_on_level == 0x01:
                    self.broadcast_scene_level = {"timestamp": time.time(),
                                                  "level": on_level}
                else:
                    # Clear saved level
                    self.broadcast_scene_level['timestamp'] = 0

                # Reason is device because we're simulating a button press.
                # We can't really pass this around because we just get a
                # broadcast message later from the device.  So we set a
                # temporary variable here and use it in handle_broadcast()
                # to output the reason.
                if reason is not None:
                    self.broadcast_reason = reason
                else:
                    self.broadcast_reason = on_off.REASON_DEVICE
            on_done(success, msg, data)
        callback = self.generic_ack_callback("Device acknowledged scene cmd.")
        msg_handler = handler.StandardCmd(msg, callback, our_on_done)
        self.send(msg, msg_handler)

    #-----------------------------------------------------------------------
    def scene_set(self, level, group=0x01, reason=None, on_done=None):
        """Set the devices in a scene to a specific level.

        This is different from scene() which simulates a button press on the
        device.  A scene command causes the responders to use the on level
        stored in their own all link databases.  This method instead sends a
        direct command to this device and to each responder in the group so
        that every device is set to the same level.

        This is useful when the desired level isn't known when the scene is
        created and so cannot be stored in the responder databases.

        The responders are found using the cached all link database of this
        device.  If the databases are out of date, refresh them first.

        The state change reasons match a scene() call: this device reports
        the device reason (or the input reason) and the responders report
        the scene reason.

        Args:
          level (int):  The dimmer level [0-255] to set the devices to.  A
                level of 0 turns the devices off.
          group (int):  The group (button) number of the scene to set.  The
                responders are found in this device's database for the group.
          reason (str):  This is optional and is used to identify why the
                command was sent.  It is passed through to this device's
                output signal when the state changes.  The responders always
                report the scene reason, just as they would for a scene()
                call.
          on_done: Finished callback.  This is called when the command has
                completed.  Signature is: on_done(success, msg, data)
        """
        LOG.info("Device %s cmd: scene set grp %s level %s", self.label,
                 group, level)
        on_done = util.make_callback(on_done)

        level = util.input_byte({'level': level}, 'level')
        if level is None:
            on_done(False, "Invalid level input.", None)
            return

        group = 0x01 if group is None else int(group)
        is_on = level > 0

        # Match the reason behavior of scene(): this device reports the
        # reason as a physical button press (or the user input) while the
        # responders always report the scene reason.
        reason = reason if reason else on_off.REASON_DEVICE

        # Only these devices can use a 0-255 dimmer level.  Other responder
        # types (switches, relays, etc) are just turned on or off.  The
        # FanLinc light (group 1) and fan (group 2) both decode the level.
        level_types = ("dimmer", "fan_linc", "keypad_linc")

        seq = CommandSeq(self, "Scene set complete", on_done,
                         error_stop=False, name="SceneSet")

        # Set this device first.  For multi-button devices this may only
        # change the state of the button rather than the load.  Scene is
        # only mixed into responder devices so set() always exists.
        level_arg = level if self.type() in level_types else None
        set_device = self.set  # pylint: disable=no-member
        seq.add(set_device, is_on=is_on, group=group, level=level_arg,
                reason=reason)

        # Set each responder that this device's database says is in the
        # group.  Each responder updates its own state from its ACK so the
        # software state matches even though no broadcast is sent.
        for entry in self.db.find_group(group):
            device = self.modem.find(entry.addr)
            if not device:
                LOG.debug("Device %s scene set - device %s is not in "
                          "config", self.label, entry.addr)
                continue

            if 'set' not in getattr(device, 'cmd_map', {}):
                LOG.debug("Device %s scene set - device %s cannot be set",
                          self.label, device.label)
                continue

            # Find the local group on the responder that this scene
            # controls.  Without the responder half of the link we don't
            # know what to change so skip the device.
            resp_entry = device.db.find(self.addr, group,
                                        is_controller=False)
            if not resp_entry:
                LOG.warning("Device %s scene set - %s has no responder "
                            "entry for group %s", self.label, device.label,
                            group)
                continue

            local_group = device.group_cmd_local_group(resp_entry)
            level_arg = level if device.type() in level_types else None
            seq.add(device.set, is_on=is_on, group=local_group,
                    level=level_arg, reason=on_off.REASON_SCENE)

        seq.run()
