import rlp
import messages
from messages import Ack, Secret, BaseError
from utils import isaddress, sha3
import gevent


class RaidenProtocol(object):

    """
    each message sent or received is stored by hash
    if message is received twice, resent previous answer
    if there is no response to a message, message gets repeated max N times
    """

    try_interval = 1.
    max_tries = 5
    max_message_size = 1200

    def __init__(self, transport, discovery, raiden_service):
        self.transport = transport
        self.discovery = discovery
        self.raiden_service = raiden_service

        self.tries = dict()  # msg hash: count_tries
        self.sent_acks = dict()  # msghash: Ack

    def send(self, receiver_address, msg):
        assert isaddress(receiver_address)
        assert not isinstance(msg, (Ack, BaseError)), msg

        host_port = self.discovery.get(receiver_address)
        msghash = msg.hash
        self.tries[msghash] = self.max_tries
        data = rlp.encode(msg)
        assert len(data) < self.max_message_size
        while self.tries.get(msghash, 0) > 0:
            # assert self.tries[msghash] == self.max_tries,  "DEACTIVATED MSG resents"
            self.tries[msghash] -= 1
            self.transport.send(self.raiden_service, host_port, data)
            gevent.sleep(self.try_interval)
        if msghash in self.tries:
            assert False, "Node does not reply, fixme suspend node"

    def send_ack(self, receiver_address, msg):
        assert isinstance(msg,  (Ack, BaseError))
        assert isaddress(receiver_address)
        host_port = self.discovery.get(receiver_address)
        self.transport.send(self.raiden_service, host_port, rlp.encode(msg))
        self.sent_acks[msg.echo] = (receiver_address, msg)

    def receive(self, data):
        assert len(data) < self.max_message_size

        # check if we handled this message already, if so repeat Ack
        h = sha3(data)
        if h in self.sent_acks:
            # assert False, "DEACTIVATED ACK RESENTS"
            return self.send_ack(*self.sent_acks[h])

        # note, we ignore the sending endpoint, as this can not be known w/ UDP
        msg = messages.deserialize(data)
        # handle Acks
        if isinstance(msg, Ack):
            del self.tries[msg.echo]
            return

        assert isinstance(msg, Secret) or msg.sender
        self.raiden_service.on_message(msg)
