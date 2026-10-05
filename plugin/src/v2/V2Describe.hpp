#pragma once

// Canonical one-line-per-item text for decoded v2 datagrams. tools/v2_golden.py produces exactly
// the same text from proto.py's decoded values, so comparing the two strings proves that both
// decoders read the same numbers out of the same bytes. It is also handy in diagnostics.
//
//   packet type=6 token=00000000000004d2 seq=7 ack=6 ack_bits=00000003
//   msg type=16 peer=255 rel=- delivery=ok minor=0 PLAYER_SNAPSHOT snap_seq=7 ... x=f:c4b54000 ...
//
// Integers are decimal, floats are their IEEE-754 binary32 bits ("f:" + 8 hex digits), text and
// byte arrays are lowercase hex of the raw bytes. Entity records are one token each:
// rec:<net_id>[:remove][:spawn=k,f,a,record,appearance][:pos=x,y,z][:pos_delta=..][:yaw=..][:quat=..]
// [:vel=..][:state=..][:target=..][:weapon=..][:world_id=..]

#include "v2/V2Codec.hpp"

#include <string>
#include <vector>

namespace coopnet::v2
{
std::string Hex(ByteSpan aBytes);
std::string Hex(std::string_view aBytes);

std::string DescribeJoin(const JoinInfo& aJoin);
std::string DescribeBody(const Body& aBody);
std::string DescribeMessage(const MessageView& aMessage, const Body& aBody);

// Decodes every layer of a datagram (packet header, handshake body or every DATA message and its
// body) and describes it. Returns the first failure; aLines then holds what was decoded so far.
Status DescribeDatagram(ByteSpan aDatagram, std::vector<std::string>& aLines);
} // namespace coopnet::v2
