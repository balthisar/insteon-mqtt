#===========================================================================
#
# Tests for: insteont_mqtt/message/OutAllLinkGetFirst.py
#
#===========================================================================
import insteon_mqtt.message as Msg


class Test_OutAllLinkGetFirst:
    #-----------------------------------------------------------------------
    def test_out(self):
        obj = Msg.OutAllLinkGetFirst()
        assert obj.fixed_msg_size == 3

        b = obj.to_bytes()
        rt = bytes([0x02, 0x69])
        assert b == rt

        str(obj)

    #-----------------------------------------------------------------------
    def test_in(self):
        b = bytes([0x02, 0x69, 0x06])
        obj = Msg.OutAllLinkGetFirst.from_bytes(b)

        assert obj.is_ack is True

        str(obj)

    #-----------------------------------------------------------------------


#===========================================================================
