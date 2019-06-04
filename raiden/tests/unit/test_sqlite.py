import itertools
import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from raiden.messages import Lock
from raiden.storage.restore import (
    get_event_with_balance_proof_by_balance_hash,
    get_event_with_balance_proof_by_locksroot,
    get_state_change_with_balance_proof_by_balance_hash,
    get_state_change_with_balance_proof_by_locksroot,
)
from raiden.storage.serialization import JSONSerializer
from raiden.storage.sqlite import (
    RANGE_ALL_STATE_CHANGES,
    Range,
    SerializedSQLiteStorage,
    SQLiteStorage,
)
from raiden.tests.utils import factories
from raiden.transfer.mediated_transfer.events import (
    SendBalanceProof,
    SendLockedTransfer,
    SendLockExpired,
    SendRefundTransfer,
)
from raiden.transfer.mediated_transfer.state_change import (
    ActionInitMediator,
    ActionInitTarget,
    ReceiveLockExpired,
    ReceiveTransferRefund,
    ReceiveTransferRefundCancelRoute,
)
from raiden.transfer.state import BalanceProofUnsignedState, RouteState
from raiden.transfer.state_change import Block, ReceiveUnlock
from raiden.utils import sha3
from raiden.utils.typing import BlockExpiration, BlockGasLimit, BlockNumber, MessageID, TokenAmount


def make_signed_balance_proof_from_counter(counter):
    lock = Lock(
        amount=next(counter),
        expiration=next(counter),
        secrethash=factories.make_secret_hash(next(counter)),
    )
    lock_expired_balance_proof = factories.create(
        factories.BalanceProofSignedStateProperties(
            nonce=next(counter),
            transferred_amount=next(counter),
            locked_amount=next(counter),
            canonical_identifier=factories.make_canonical_identifier(
                token_network_address=factories.make_address(), channel_identifier=next(counter)
            ),
            locksroot=sha3(lock.as_bytes),
            message_hash=sha3(b""),
            sender=factories.HOP1,
            pkey=factories.HOP1_KEY,
        )
    )

    return lock_expired_balance_proof


def make_balance_proof_from_counter(counter) -> BalanceProofUnsignedState:
    return BalanceProofUnsignedState(
        nonce=next(counter),
        transferred_amount=next(counter),
        locked_amount=next(counter),
        locksroot=sha3(next(counter).to_bytes(1, "big")),
        canonical_identifier=factories.make_canonical_identifier(
            chain_identifier=next(counter),
            token_network_address=factories.make_address(),
            channel_identifier=next(counter),
        ),
    )


def make_transfer_from_counter(counter):
    return factories.create(
        factories.LockedTransferUnsignedStateProperties(
            amount=next(counter),
            initiator=factories.make_initiator_address(),
            target=factories.make_target_address(),
            expiration=next(counter),
            secret=factories.make_secret(next(counter)),
        )
    )


def make_signed_transfer_from_counter(counter):
    lock = Lock(
        amount=next(counter),
        expiration=next(counter),
        secrethash=factories.make_secret_hash(next(counter)),
    )

    signed_transfer = factories.create(
        factories.LockedTransferSignedStateProperties(
            amount=next(counter),
            initiator=factories.make_initiator_address(),
            target=factories.make_target_address(),
            expiration=next(counter),
            secret=factories.make_secret(next(counter)),
            payment_identifier=next(counter),
            token=factories.make_token_address(),
            nonce=next(counter),
            transferred_amount=next(counter),
            locked_amount=next(counter),
            locksroot=sha3(lock.as_bytes),
            canonical_identifier=factories.make_canonical_identifier(
                token_network_address=factories.make_address(), channel_identifier=next(counter)
            ),
            recipient=factories.make_address(),
            sender=factories.HOP1,
            pkey=factories.HOP1_KEY,
        )
    )

    return signed_transfer


def make_from_route_from_counter(counter):
    from_channel = factories.create(
        factories.NettingChannelStateProperties(
            canonical_identifier=factories.make_canonical_identifier(),
            token_address=factories.make_token_address(),
            partner_state=factories.NettingChannelEndStateProperties(
                balance=next(counter), address=factories.HOP1
            ),
        )
    )
    from_hop = factories.make_hop_from_channel(from_channel)

    expiration = BlockExpiration(factories.UNIT_REVEAL_TIMEOUT + 1)

    from_transfer = factories.make_signed_transfer_for(
        from_channel,
        factories.LockedTransferSignedStateProperties(
            transferred_amount=TokenAmount(0),
            canonical_identifier=factories.make_canonical_identifier(
                token_network_address=from_channel.token_network_address
            ),
            amount=TokenAmount(1),
            expiration=expiration,
            secret=sha3(factories.make_secret(next(counter))),
            initiator=factories.make_initiator_address(),
            target=factories.make_target_address(),
            payment_identifier=next(counter),
            sender=factories.HOP1,
            pkey=factories.HOP1_KEY,
        ),
    )
    return from_hop, from_transfer


def test_get_state_change_with_balance_proof():
    """ All state changes which contain a balance proof must be found when
    querying the database.
    """
    serializer = JSONSerializer()
    storage = SerializedSQLiteStorage(":memory:", serializer)
    counter = itertools.count()

    balance_proof = make_signed_balance_proof_from_counter(counter)

    lock_expired = ReceiveLockExpired(
        sender=balance_proof.sender,
        balance_proof=balance_proof,
        secrethash=factories.make_secret_hash(next(counter)),
        message_identifier=MessageID(next(counter)),
    )

    received_balance_proof = make_signed_balance_proof_from_counter(counter)
    unlock = ReceiveUnlock(
        sender=received_balance_proof.sender,
        message_identifier=MessageID(next(counter)),
        secret=factories.make_secret(next(counter)),
        balance_proof=received_balance_proof,
    )
    transfer = make_signed_transfer_from_counter(counter)
    transfer_refund = ReceiveTransferRefund(
        transfer=transfer,
        balance_proof=transfer.balance_proof,
        sender=transfer.balance_proof.sender,  # pylint: disable=no-member
        routes=list(),
    )
    transfer = make_signed_transfer_from_counter(counter)
    transfer_refund_cancel_route = ReceiveTransferRefundCancelRoute(
        routes=list(),
        transfer=transfer,
        balance_proof=transfer.balance_proof,
        sender=transfer.balance_proof.sender,  # pylint: disable=no-member
        secret=sha3(factories.make_secret(next(counter))),
    )
    mediator_from_route, mediator_signed_transfer = make_from_route_from_counter(counter)

    action_init_mediator = ActionInitMediator(
        route_states=[
            RouteState(
                route=[factories.make_address(), factories.make_address()],
                forward_channel_id=factories.make_channel_identifier(),
            )
        ],
        from_hop=mediator_from_route,
        from_transfer=mediator_signed_transfer,
        balance_proof=mediator_signed_transfer.balance_proof,
        sender=mediator_signed_transfer.balance_proof.sender,  # pylint: disable=no-member
    )
    target_from_route, target_signed_transfer = make_from_route_from_counter(counter)
    action_init_target = ActionInitTarget(
        from_hop=target_from_route,
        transfer=target_signed_transfer,
        balance_proof=target_signed_transfer.balance_proof,
        sender=target_signed_transfer.balance_proof.sender,  # pylint: disable=no-member
    )

    statechanges_balanceproofs = [
        (lock_expired, lock_expired.balance_proof),
        (unlock, unlock.balance_proof),
        (transfer_refund, transfer_refund.transfer.balance_proof),
        (transfer_refund_cancel_route, transfer_refund_cancel_route.transfer.balance_proof),
        (action_init_mediator, action_init_mediator.from_transfer.balance_proof),
        (action_init_target, action_init_target.transfer.balance_proof),
    ]

    timestamp = datetime.utcnow()

    assert storage.count_state_changes() == 0

    for state_change, _ in statechanges_balanceproofs:
        storage.write_state_change(state_change, timestamp)

    assert storage.count_state_changes() == len(statechanges_balanceproofs)

    # Make sure state changes are returned in the correct order in which they were stored
    stored_statechanges_records = storage.get_statechanges_records_by_range(
        RANGE_ALL_STATE_CHANGES
    )
    assert len(stored_statechanges_records) == 6
    assert isinstance(stored_statechanges_records[0].data, ReceiveLockExpired)
    assert isinstance(stored_statechanges_records[1].data, ReceiveUnlock)
    assert isinstance(stored_statechanges_records[2].data, ReceiveTransferRefund)
    assert isinstance(stored_statechanges_records[3].data, ReceiveTransferRefundCancelRoute)
    assert isinstance(stored_statechanges_records[4].data, ActionInitMediator)
    assert isinstance(stored_statechanges_records[5].data, ActionInitTarget)

    # Make sure state changes are returned in the correct order in which they were stored
    stored_statechanges = storage.get_statechanges_by_range(
        Range(
            stored_statechanges_records[1].state_change_identifier,
            stored_statechanges_records[2].state_change_identifier,
        )
    )

    assert len(stored_statechanges) == 2
    assert isinstance(stored_statechanges[0], ReceiveUnlock)
    assert isinstance(stored_statechanges[1], ReceiveTransferRefund)

    for state_change, balance_proof in statechanges_balanceproofs:
        state_change_record = get_state_change_with_balance_proof_by_balance_hash(
            storage=storage,
            canonical_identifier=balance_proof.canonical_identifier,
            sender=balance_proof.sender,
            balance_hash=balance_proof.balance_hash,
        )
        assert state_change_record
        assert state_change_record.data == state_change

        state_change_record = get_state_change_with_balance_proof_by_locksroot(
            storage=storage,
            canonical_identifier=balance_proof.canonical_identifier,
            sender=balance_proof.sender,
            locksroot=balance_proof.locksroot,
        )
        assert state_change_record
        assert state_change_record.data == state_change

    storage.close()


def test_get_event_with_balance_proof():
    """ All events which contain a balance proof must be found by when
    querying the database.
    """
    serializer = JSONSerializer()
    storage = SerializedSQLiteStorage(":memory:", serializer)
    counter = itertools.count(1)
    partner_address = factories.make_address()

    balance_proof = make_balance_proof_from_counter(counter)
    lock_expired = SendLockExpired(
        recipient=partner_address,
        message_identifier=MessageID(next(counter)),
        balance_proof=balance_proof,
        secrethash=factories.make_secret_hash(next(counter)),
        canonical_identifier=balance_proof.canonical_identifier,
    )
    locked_transfer = SendLockedTransfer(
        recipient=partner_address,
        message_identifier=MessageID(next(counter)),
        transfer=make_transfer_from_counter(counter),
        canonical_identifier=factories.make_canonical_identifier(),
    )
    send_balance_proof = SendBalanceProof(
        recipient=partner_address,
        message_identifier=MessageID(next(counter)),
        payment_identifier=factories.make_payment_id(),
        token_address=factories.make_token_address(),
        secret=factories.make_secret(next(counter)),
        balance_proof=make_balance_proof_from_counter(counter),
        canonical_identifier=factories.make_canonical_identifier(),
    )

    refund_transfer = SendRefundTransfer(
        recipient=partner_address,
        message_identifier=MessageID(next(counter)),
        transfer=make_transfer_from_counter(counter),
        canonical_identifier=factories.make_canonical_identifier(),
    )

    events_balanceproofs = [
        (lock_expired, lock_expired.balance_proof),
        (locked_transfer, locked_transfer.balance_proof),
        (send_balance_proof, send_balance_proof.balance_proof),
        (refund_transfer, refund_transfer.transfer.balance_proof),
    ]

    timestamp = datetime.utcnow()
    state_change = Block(BlockNumber(1), BlockGasLimit(1), factories.make_block_hash())
    for event, _ in events_balanceproofs:
        state_change_identifier = storage.write_state_change(state_change, timestamp)
        storage.write_events(
            state_change_identifier=state_change_identifier, events=[event], timestamp=timestamp
        )

    for event, balance_proof in events_balanceproofs:
        event_record = get_event_with_balance_proof_by_balance_hash(
            storage=storage,
            canonical_identifier=balance_proof.canonical_identifier,
            balance_hash=balance_proof.balance_hash,
            recipient=partner_address,
        )
        assert event_record
        assert event_record.data == event

        event_record = get_event_with_balance_proof_by_locksroot(
            storage=storage,
            canonical_identifier=balance_proof.canonical_identifier,
            recipient=event.recipient,
            locksroot=balance_proof.locksroot,
        )
        assert event_record
        assert event_record.data == event

        # Checking that balance proof attribute can be accessed for all events.
        # Issue https://github.com/raiden-network/raiden/issues/3179
        assert event_record.data.balance_proof == event.balance_proof  # type: ignore

    storage.close()


def test_log_run():
    with patch("raiden.storage.sqlite.get_system_spec") as get_speck_mock:
        get_speck_mock.return_value = dict(raiden="1.2.3")
        serializer = JSONSerializer()
        store = SerializedSQLiteStorage(":memory:", serializer)
        store.log_run()
    cursor = store.database.conn.cursor()
    cursor.execute("SELECT started_at, raiden_version FROM runs")
    run = cursor.fetchone()
    now = datetime.utcnow()
    assert now - timedelta(seconds=2) <= run[0] <= now, f"{run[0]} not right before {now}"
    assert run[1] == "1.2.3"

    store.close()


@pytest.fixture
def storage():
    state_changes_file = Path(__file__).parent / "test_data" / "db_statechanges.json"
    state_changes_data = json.loads(state_changes_file.read_text())

    with SQLiteStorage(":memory:") as storage:
        state_change_identifiers = list()
        for state_change_record in state_changes_data:
            state_change_id = storage.write_state_change(
                state_change=json.dumps(state_change_record[1]), timestamp=datetime.utcnow()
            )
            state_change_identifiers.append(state_change_id)

        yield storage


def test_batch_query_state_changes():
    state_changes_file = Path(__file__).parent / "test_data" / "db_statechanges.json"
    state_changes_data = json.loads(state_changes_file.read_text())

    storage = SQLiteStorage(":memory:")
    state_change_identifiers = list()
    for state_change_record in state_changes_data:
        state_change_id = storage.write_state_change(
            state_change=json.dumps(state_change_record[1]), timestamp=datetime.utcnow()
        )
        state_change_identifiers.append(state_change_id)

    # Test that querying the state changes in batches of 10 works
    state_changes_num = 87
    state_changes = []
    for state_changes_batch in storage.batch_query_state_changes(batch_size=10):
        state_changes.extend(state_changes_batch)

    assert len(state_changes) == state_changes_num
    for pos, id_ in enumerate(state_change_identifiers):
        assert state_changes[pos].state_change_identifier == id_

    # Test that we can also add a filter
    state_changes = []
    state_changes_batch_query = storage.batch_query_state_changes(
        batch_size=10, filters=[("_type", "raiden.transfer.state_change.Block")]
    )
    for state_changes_batch in state_changes_batch_query:
        state_changes.extend(state_changes_batch)
    assert len(state_changes) == 77

    # Test that filter works with logical or and a wildmark too
    state_changes = []
    state_changes_batch_query = storage.batch_query_state_changes(
        batch_size=10,
        filters=[
            # Should be 5 of them
            ("_type", "raiden.transfer.state_change.ContractReceiveChannel%"),
            # Should be only 1
            ("_type", "raiden.transfer.state_change.ContractReceiveNewPaymentNetwork"),
        ],
        logical_and=False,
    )
    for state_changes_batch in state_changes_batch_query:
        state_changes.extend(state_changes_batch)
    assert len(state_changes) == 6

    storage.close()


def test_batch_query_event_records():
    storage = SQLiteStorage(":memory:")

    state_changes_file = Path(__file__).parent / "test_data" / "db_statechanges.json"
    state_changes_data = json.loads(state_changes_file.read_text())
    state_change_identifiers = list()
    for state_change_record in state_changes_data:
        state_change_id = storage.write_state_change(
            state_change=json.dumps(state_change_record[1]), timestamp=datetime.utcnow()
        )
        state_change_identifiers.append(state_change_id)

    events_file = Path(__file__).parent / "test_data" / "db_events.json"
    events_data = json.loads(events_file.read_text())
    for event in events_data:
        state_change_id = state_change_identifiers[event[1]]
        event_data = json.dumps(event[2])
        event_tuple = (state_change_id, datetime.utcnow(), event_data)
        storage.write_events([event_tuple])

    # Test that querying the events in batches of 1 works
    events = []
    for events_batch in storage.batch_query_event_records(batch_size=1):
        events.extend(events_batch)
    assert len(events) == 3

    # Test that we can also add a filter
    events = []
    events_batch_query = storage.batch_query_event_records(
        batch_size=1, filters=[("_type", "raiden.transfer.events.EventPaymentReceivedSuccess")]
    )
    for events_batch in events_batch_query:
        events.extend(events_batch)
    assert len(events) == 1
    event_type = json.loads(events[0].data)["_type"]
    assert event_type == "raiden.transfer.events.EventPaymentReceivedSuccess"

    # Test that we can also add a filter with logical OR
    events = []
    events_batch_query = storage.batch_query_event_records(
        batch_size=1,
        filters=[
            ("_type", "raiden.transfer.events.EventPaymentReceivedSuccess"),
            ("_type", "raiden.transfer.events.ContractSendChannelSettle"),
        ],
        logical_and=False,
    )
    for events_batch in events_batch_query:
        events.extend(events_batch)
    assert len(events) == 2

    storage.close()


def test_update_event_get_event():
    storage = SQLiteStorage(":memory:")
    state_changes_file = Path(__file__).parent / "test_data" / "db_statechanges.json"
    state_changes_data = json.loads(state_changes_file.read_text())
    for state_change_record in state_changes_data:
        storage.write_state_change(
            state_change=json.dumps(state_change_record[1]), timestamp=datetime.utcnow()
        )


def test_storage_get_and_update(storage):
    other_storage = SQLiteStorage(":memory:")
    data = storage.get_events()
    event_data = [(item, number) for number, item in enumerate(data)]
    other_storage.update_events(event_data)
    assert other_storage


def test_storage_close():
    storage = SerializedSQLiteStorage(":memory:", JSONSerializer())
    storage.close()
    with pytest.raises(RuntimeError):  # attempt to close an already closed database
        storage.close()
