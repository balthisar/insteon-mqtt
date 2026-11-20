#===========================================================================
#
# MQTT Scene Topic
#
#===========================================================================
import functools
from ... import log
from ..MsgTemplate import MsgTemplate
from .. import util
from .BaseTopic import BaseTopic

LOG = log.get_logger()

# Default input payload template for the scene_set command.  The template
# accepts the explicit scene_set command form, a HomeAssistant JSON light
# command (state/brightness), or a plain on/off payload and converts all of
# them to the { "level" : 0-255, ["group" : N], ["reason" : "..."] } form.
# This must stay in sync with the scene_set_payload in config-base.yaml.
DEFAULT_SCENE_SET_PAYLOAD = (
    '{ "level" : '
    '{%- if json and json.level is defined -%}{{ json.level }}'
    '{%- elif json and json.state is defined and '
    'json.state|string|lower == "off" -%}0'
    '{%- elif json and json.state is defined and '
    'json.brightness is defined -%}{{ json.brightness }}'
    '{%- elif json and json.state is defined and '
    'json.state|string|lower == "on" -%}255'
    '{%- elif json and json.brightness is defined -%}{{ json.brightness }}'
    '{%- elif value is defined and value|string|lower == "off" -%}0'
    '{%- elif value is defined and value|string|lower == "on" -%}255'
    '{%- else -%}{{ value }}'
    '{%- endif -%}'
    '{%- if json and json.group is defined -%}'
    ', "group" : {{ json.group }}'
    '{%- endif -%}'
    '{%- if json and json.reason is defined -%}'
    ', "reason" : "{{ json.reason }}"'
    '{%- endif -%} }')


class SceneTopic(BaseTopic):
    """MQTT interface to the Scene Topic

    This is an abstract class that provides support for the Scene topic.
    """
    def __init__(self, mqtt, device, scene_topic=None, scene_payload=None,
                 scene_set_topic=None, scene_set_payload=None, **kwargs):
        """Scene Topic Constructor

        Args:
          mqtt (mqtt.Mqtt):  The MQTT main interface.
          device (device):  The Insteon object to link to.
          scene_topic (str): The template for the topic
          scene_payload (str): The template for the payload
          scene_set_topic (str): The template for the scene_set topic
          scene_set_payload (str): The template for the scene_set payload
        """
        super().__init__(mqtt, device, **kwargs)
        # It looks cleaner setting these long strings here rather than in the
        # function declaration
        if scene_topic is None:
            scene_topic = 'insteon/{{address}}/scene'
        if scene_payload is None:
            scene_payload = '{ "cmd" : "{{json.cmd.lower()}}"' \
                            '{% if json.brightness is defined %}' \
                            '  , "level" : {{json.brightness}}' \
                            '{% endif %}' \
                            '}'
        # Input scene on/off command template.
        self.msg_scene = MsgTemplate(
            topic=scene_topic,
            payload=scene_payload)

        if scene_set_topic is None:
            scene_set_topic = 'insteon/{{address}}/scene_set'
        if scene_set_payload is None:
            scene_set_payload = DEFAULT_SCENE_SET_PAYLOAD
        # Input scene set command template.
        self.msg_scene_set = MsgTemplate(
            topic=scene_set_topic,
            payload=scene_set_payload)

    #-----------------------------------------------------------------------
    def load_scene_data(self, data, qos=None, topic=None, payload=None):
        """Load values from a configuration data object.

        Args:
          data (dict):  The section of the config dict that applies to this
                        class.
          qos (int):  The default quality of service level to use.
        """
        if topic is None:
            topic = 'scene_topic'
        if payload is None:
            payload = 'scene_payload'
        # Update the MQTT topics and payloads from the config file.
        self.msg_scene.load_config(data, topic, payload, qos)

        # Add ourselves to the list of topics
        if len(self.extra_topic_nums) > 0:
            # This device has multiple scene topics for multiple buttons
            data = self.base_template_data()
            topics = {}
            for btn in self.extra_topic_nums:
                data['button'] = btn
                topics[topic + "_" + str(btn)] = self.msg_scene.render_topic(
                    data
                )
            self.rendered_topic_map.update(topics)
        else:
            # Add ourselves to the list of topics
            self.rendered_topic_map[topic] = self.msg_scene.render_topic(
                self.base_template_data()
            )

    #-----------------------------------------------------------------------
    def load_scene_set_data(self, data, qos=None, topic=None, payload=None):
        """Load values from a configuration data object.

        Args:
          data (dict):  The section of the config dict that applies to this
                        class.
          qos (int):  The default quality of service level to use.
          topic (str):  The name of the topic field in the config dict.
          payload (str):  The name of the payload field in the config dict.
        """
        if topic is None:
            topic = 'scene_set_topic'
        if payload is None:
            payload = 'scene_set_payload'
        # Update the MQTT topics and payloads from the config file.
        self.msg_scene_set.load_config(data, topic, payload, qos)

        # Add ourselves to the list of topics
        if len(self.extra_topic_nums) > 0:
            # This device has multiple scene set topics for multiple buttons
            data = self.base_template_data()
            topics = {}
            for btn in self.extra_topic_nums:
                data['button'] = btn
                topics[topic + "_" + str(btn)] = \
                    self.msg_scene_set.render_topic(data)
            self.rendered_topic_map.update(topics)
        else:
            # Add ourselves to the list of topics
            self.rendered_topic_map[topic] = \
                self.msg_scene_set.render_topic(self.base_template_data())

    #-----------------------------------------------------------------------
    def scene_subscribe(self, link, qos, group=None):
        """Subscribe to any MQTT topics the object needs.

        Subscriptions are used when the object has things that can be
        commanded to change.

        Args:
          link (network.Mqtt):  The MQTT network client to use.
          qos (int):  The quality of service to use.
        """
        # Scene triggering messages.
        if group is not None:
            handler = functools.partial(self._input_scene, group=group)
            topic = self.msg_scene.render_topic(
                self.base_template_data(button=group)
            )
        else:
            handler = self._input_scene
            topic = self.msg_scene.render_topic(self.base_template_data())
        link.subscribe(topic, qos, handler)

    #-----------------------------------------------------------------------
    def scene_unsubscribe(self, link, group=None):
        """Unsubscribe to any MQTT topics the object was subscribed to.

        Args:
          link (network.Mqtt):  The MQTT network client to use.
        """
        if group is not None:
            data = self.base_template_data(button=group)
            topic = self.msg_scene.render_topic(data)
        else:
            topic = self.msg_scene.render_topic(self.base_template_data())
        link.unsubscribe(topic)

    #-----------------------------------------------------------------------
    def scene_set_subscribe(self, link, qos, group=None):
        """Subscribe to any MQTT topics the object needs.

        Subscriptions are used when the object has things that can be
        commanded to change.

        Args:
          link (network.Mqtt):  The MQTT network client to use.
          qos (int):  The quality of service to use.
          group (int):  The group (button) number to bind to the topic.  None
               for the default topic.
        """
        # Scene setting messages.
        if group is not None:
            handler = functools.partial(self._input_scene_set, group=group)
            topic = self.msg_scene_set.render_topic(
                self.base_template_data(button=group)
            )
        else:
            handler = self._input_scene_set
            topic = self.msg_scene_set.render_topic(self.base_template_data())
        # An empty topic is used to disable the input.
        if topic:
            link.subscribe(topic, qos, handler)

    #-----------------------------------------------------------------------
    def scene_set_unsubscribe(self, link, group=None):
        """Unsubscribe to any MQTT topics the object was subscribed to.

        Args:
          link (network.Mqtt):  The MQTT network client to use.
          group (int):  The group (button) number that was bound to the topic.
        """
        if group is not None:
            data = self.base_template_data(button=group)
            topic = self.msg_scene_set.render_topic(data)
        else:
            topic = self.msg_scene_set.render_topic(self.base_template_data())
        if topic:
            link.unsubscribe(topic)

    #-----------------------------------------------------------------------
    def _input_scene_set(self, client, data, message, group=None):
        """Handle an input scene_set MQTT message.

        This is called when we receive a message on the scene set MQTT
        topic subscription.  Parse the message and set the device and all of
        its scene responders to the requested level.

        Args:
          client (paho.Client):  The paho mqtt client (self.link).
          data:  Optional user data (unused).
          message:  MQTT message - has attrs: topic, payload, qos, retain.
          group (int):  The group (button) number bound to the topic.  The
               payload group, if present, takes precedence.
        """
        LOG.debug("SceneSetTopic message %s %s", message.topic,
                  message.payload)

        # Parse the input MQTT message.
        data = self.msg_scene_set.to_json(message.payload)
        LOG.info("SceneSetTopic input command: %s", data)
        if not data:
            LOG.error("Invalid SceneSetTopic command: %s", message.payload)
            return
        try:
            level = data.get('level', None)
            if level is None:
                raise ValueError("Missing level input")
            level = int(level)
            if level < 0 or level > 255:
                raise ValueError("Level out of range")

            if 'group' in data:
                group = int(data.get('group'))
            if group is None:
                group = 0x01

            # Make sure the device supports the requested group before
            # passing it along.  Otherwise the device set() call would assert.
            responder_groups = getattr(self.device, 'responder_groups', None)
            if responder_groups is not None and group not in responder_groups:
                LOG.error("Device %s does not support scene group %s",
                          self.device.label, group)
                return

            reason = data.get("reason", None)

            # Tell the device to set the scene to the level.
            self.device.scene_set(level=level, group=group, reason=reason)
        except:
            LOG.error("Invalid SceneSetTopic command: %s", data)

    #-----------------------------------------------------------------------
    def _input_scene(self, client, data, message, group=None):
        """Handle an input scene MQTT message.

        This is called when we receive a message on the scene trigger MQTT
        topic subscription.  Parse the message and pass the command to the
        Insteon device.

        Args:
          client (paho.Client):  The paho mqtt client (self.link).
          data:  Optional user data (unused).
          message:  MQTT message - has attrs: topic, payload, qos, retain.
        """
        LOG.debug("SceneTopic message %s %s", message.topic, message.payload)

        # Parse the input MQTT message.
        data = self.msg_scene.to_json(message.payload)
        LOG.info("SceneTopic input command: %s", data)
        try:
            # Scenes don't support modes so don't parse that element.
            is_on = util.parse_on_off(data, have_mode=False)
            if 'group' in data:
                group = int(data.get('group'))
            name = data.get("name", None)
            reason = data.get("reason", None)
            level = data.get("level", None)

            # Tell the device to trigger the scene command.
            self.device.scene(is_on, level=level, group=group, name=name,
                              reason=reason)
        except:
            LOG.error("Invalid SceneTopic command: %s", data)

    #-----------------------------------------------------------------------
