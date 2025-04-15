# Chapter 3: Data Link Layer

_(Based on PDF: Computer_Network_Chapter_3.pdf)_

---

## Overview & Introduction (Slides 1-5)

- **Functionalities**: Encapsulation, addressing, Error detection and correction, Flow control, Media access control.
- **Introduction**:
  - **Terminology**:
    - Nodes: Hosts and routers.
    - Links: Communication channels connecting adjacent nodes (wired, wireless, LANs).
    - Frame: Layer-2 packet, encapsulates datagram.
  - **Responsibility**: Transferring datagram from one node to a physically adjacent node over a link.
- **Datalink Layer in Layer Architecture**:
  - Positioned between Network and Physical layers.
  - Often split into sublayers:
    - **LLC (Logical Link Control)**: Media independent (e.g., IEEE 802.2).
    - **MAC (Media Access Control)**: Media dependent (e.g., IEEE 802.3 Ethernet, 802.5 Token Ring, 802.11 Wi-Fi, 802.16 Wi-Max).

## Functionalities & Context (Slides 6-9)

- **Core Functionalities**: Framing, Addressing, Error control, Flow control, Media Access Control.
- **Link Layer Context**:
  - Datagrams are transferred by different link protocols over different links in a path (e.g., WiFi -> Ethernet).
  - Each link protocol provides different services (e.g., reliable vs. unreliable transfer).
  - **Transportation Analogy**:
    - Tourist = Datagram
    - Transport segment = Communication link
    - Transportation mode = Link-layer protocol
    - Travel agent = Routing algorithm
- **Link Layer Services**:
  - **Framing, Link Access**: Encapsulate datagram, add header/trailer. Channel access if shared medium. MAC addresses identify source/destination.
  - **Media Access Control**: Required if nodes share common media.
  - **Flow Control**: Pacing between adjacent nodes.
  - **Error Detection**: Detect errors (due to noise, attenuation), signal for retransmission or drop frame.
  - **Error Correction**: Receiver identifies and corrects bit errors without retransmission.
  - **Half-duplex/Full-duplex**: Half-duplex allows transmission from both ends, but not simultaneously.

## Implementation & MAC Addresses (Slides 10-12)

- **Where is the link layer implemented?**:
  - In every host and router.
  - Typically in the Network Interface Card (NIC) or on a chip (e.g., Ethernet card, WiFi chip).
  - Implements both Link and Physical layers.
  - Attaches to the host's system buses.
  - Combination of hardware, software, firmware.
- **Interfaces Communicating**:
  - **Sending side**: Encapsulates datagram in frame, adds error checking, handles reliable transfer/flow control.
  - **Receiving side**: Checks for errors, handles reliable transfer/flow control, extracts datagram, passes to the upper layer.
- **Identifier: MAC Address**:
  - 48-bit address, organized by IEEE.
  - Each port assigned one MAC.
  - Physical address, cannot be changed (typically).
  - Flat (non-hierarchical) addressing system.
  - MAC address remains unchanged when moving between networks.
  - Broadcast address in LAN: FF-FF-FF-FF-FF-FF.

## Error Control (Slides 16-38)

### Error Detection Principles (Slide 17)

- Sender adds EDC (Error Detection and Correction) bits to data D.
- Receiver checks received data D' and EDC'.
- Not 100% reliable, but larger EDC fields yield better detection/correction.

### Parity Code (Slides 20-21)

- A check bit added to ensure the total number of 1s is even (even parity) or odd (odd parity).
- **Single Parity**: Detects single bit errors.
- **Two-Dimension Parity**: Can detect and correct single-bit errors. (Example shown with a grid of data bits and parity bits for rows/columns).
- **Application**: Hardware level (e.g., PCI, SCSI bus).
- **Example (Odd Parity)**:
  - Data: 01010101 -> Code: 1 (5 ones, need 1 more for odd count)
  - Received: 01110101, Code: 1 -> Data has 6 ones (even) -> Mismatch -> Error detected.
  - Received: 01110100, Code: 1 -> Data has 5 ones (odd) -> Match -> No error detected (but could be multiple errors).

### Checksum (Slides 22-25)

- **Goal**: Detect flipped bits.
- **Sender**:
  1.  Divide data into n-bit segments.
  2.  Calculate the 1's complement sum of segments (add overflow bits back).
  3.  The checksum is the 1's complement of this sum.
- **Receiver**:
  1.  Divide received data (including checksum) into n-bit segments.
  2.  Calculate the 1's complement sum of all segments (including the checksum).
  3.  If the result is all 1s (i.e., -0 in 1's complement), no error detected.
- **Example (4-bit checksum)**:
  - Data: 0011 0110 1000
  - Sum: 0011 + 0110 = 1001. 1001 + 1000 = (1) 0001. Add carry: 0001 + 1 = 0010.
  - Checksum (1's complement): 1101.
  - Send: 0011 0110 1000 1101.
  - Receiver check: 0011 + 0110 + 1000 + 1101 = (1) 1110. Add carry: 1110 + 1 = 1111. -> No error detected.

### Cyclic Redundancy Check (CRC) (Slides 27-33)

- More powerful error detection.
- Uses a pre-agreed `r+1` bit generator pattern, G.
- **Sender**:
  1.  Append `r` zero bits to the `d` data bits D (effectively D \* 2^r).
  2.  Divide D \* 2^r by G using modulo-2 division.
  3.  The remainder R (r bits) is the CRC.
  4.  Send `<D, R>`. The bit pattern `<D, R>` is D \* 2^r XOR R.
- **Receiver**:
  1.  Divide the received `<D', R'>` by G using modulo-2 division.
  2.  If the remainder is non-zero, an error is detected.
- Can detect all burst errors less than `r+1` bits.
- Widely used (Ethernet, 802.11 WiFi, ATM).
- **Calculation of R**: R = (D \* 2^r) mod G (using modulo-2 arithmetic).
- **Example**: D=10101001, G=1001 (r=3).
  - D \* 2^3 = 10101001000.
  - 10101001000 mod 1001 = 110 (R). (Detailed division shown on slide 28).
  - Send: 10101001 110.
- **CRC Polynomial Form**: Bit patterns represented as polynomials (e.g., 1011 <-> x^3 + x + 1). Standard generator polynomials exist (CRC-8, CRC-16, CRC-32). Longer G provides better error detection.
- **Example (Polynomial)**: Frame: 1101011011, Generator G(x) = x^4 + x + 1 (P = 10011). R = 1110. Send: 11010110111110. (Division examples shown on slides 31-33).

### Reaction to Errors: Automatic Repeat Request (ARQ) (Slides 34-40)

- **Goal**: Ensure correct data reception over unreliable channels.
- **Constraints**: Correct reception, negligible delay.
- **Possible Errors**: Frame loss, error in frame, loss of ACK/NAK.
- **Techniques**: Error detection, Acknowledgement (ACK)/Negative ACK (NAK), Retransmission (on NAK or timeout).
- **ARQ Versions**: Stop-and-Wait ARQ, Go-Back-N ARQ, Selective Reject ARQ.
- **Stop-and-Wait ARQ**:
  - Sender sends packet 0, waits for ACK 0.
  - Receiver sends ACK 0 upon correct reception.
  - Sender sends packet 1 upon receiving ACK 0.
  - If packet is corrupted, receiver sends NAK. Sender resends.
  - If ACK/NAK is corrupted, sender might not know.
  - **Duplicate packets**: If ACK is lost/delayed, sender times out and resends. Receiver might get duplicates.
  - **Solution**: Use Sequence Numbers (Seq#). Packets are numbered (e.g., 0, 1, 0, 1...). Receiver discards duplicates with same Seq#.
  - ACKs carry the Seq# of the packet being acknowledged (e.g., ACK0, ACK1). ACK n implicitly confirms all packets < n received correctly.
  - **NAK-free**: Can operate without NAKs by relying solely on timeouts.
  - **Timeout Mechanism**: Sender starts a timer after sending. If ACK not received before timeout, resend. Timeout value should be >= 1 RTT (Round Trip Time). (Diagrams on slides 39-40 illustrate timeout scenarios for lost data and lost ACKs).

## Flow Control (Slides 41-52)

- **Goal**: Ensure sender doesn't overload receiver buffer.
- **Why Overloading?**: Receiver needs time to process frames and has finite buffer space. Full buffer leads to dropped frames.
- Assumes error-free transmission for explaining the concept.
- **Solutions**: Stop-and-Wait, Sliding Window.
- **Stop-and-Wait Flow Control**:
  - Sender sends one frame.
  - Waits for ACK from receiver (indicating readiness for the next frame) before sending the next frame.
  - **Advantage**: Simple.
  - **Weakness**: Inefficient, especially for small frames or long propagation delays (channel idle time). Not suitable for large frames due to buffer limits and higher error probability.
- **Sliding Window Flow Control**:
  - Allows transmission of multiple frames (up to a window size W) without waiting for individual ACKs.
  - Sender maintains a buffer of unacknowledged frames.
  - Receiver acknowledges frames (often cumulatively). ACK `k` implies frames up to `k-1` are received correctly.
  - Sender "slides" its window forward upon receiving ACKs, allowing new frames to be sent.
  - Requires frame numbering (modulo >= W).
  - **Window Management**:
    - Sender tracks: frames sent & ACKed, frames sent & not ACKed, frames ready to send, frames not yet ready.
    - Receiver tracks: frames received, frames expected.
  - **Piggybacking**: If data flows in both directions (full-duplex), ACKs can be embedded in the header of data frames going the other way. Saves bandwidth compared to sending separate ACK frames.
  - **Efficiency**: Much more efficient than Stop-and-Wait by keeping the channel busier.
  - **Complexity**: More complex state management.

## Exercise Example (Slide 53)

- Link Rate R = 100 Mbps
- File Size L = 100 KB
- Frame Size = 1 KB (ignore header)
- RTT = 3 ms
- ACK size negligible.
- **Question**: Calculate transmission time for Stop-and-Wait and Sliding Window (W=7). Find the optimal window size.
- **Stop-and-Wait Analysis (Slide 54)**:
  - Time per frame = T_transmit + RTT
  - T_transmit = Frame Size / R = 1 KB / 100 Mbps = (1 _ 8 _ 1024) bits / (100 \* 10^6) bps ≈ 0.082 ms.
  - Number of frames = File Size / Frame Size = 100 KB / 1 KB = 100 frames.
  - Total Time = Number of frames _ (T_transmit + RTT) = 100 _ (0.082 ms + 3 ms) ≈ 100 \* 3.082 ms = 308.2 ms.
- _(Sliding Window calculation not shown in detail on slides)_

## Media Access Control (MAC) (Slides 56-78)

- **Connection Types**:
  - **Point-to-point**: Dedicated link (e.g., ADSL modem, Leased Line).
  - **Broadcast**: Shared medium (e.g., old bus Ethernet, Wireless LAN, HFC upstream). Requires MAC protocol.
- **Multiple Access Links & Protocols**:
  - **Problem**: Collisions occur if two or more nodes transmit simultaneously on a shared channel.
  - **MAC Protocol**: Distributed algorithm determining how nodes share the channel. Must use the channel itself for coordination (no out-of-band signaling).
- **Ideal MAC Protocol**:
  1.  Single node transmits at full rate R.
  2.  M nodes transmit at average rate R/M each.
  3.  Fully decentralized (no master node, no clock sync needed).
  4.  Simple.
- **MAC Protocol Taxonomy**:
  - **Channel Partitioning**: Divide channel into pieces (time slots, frequency, code) allocated exclusively. (TDMA, FDMA, CDMA).
  - **Random Access**: Allow collisions, recover from them. (Aloha, CSMA/CD, CSMA/CA).
  - **Taking Turns (Sequence Access)**: Nodes take turns, potentially variable length. (Token Ring, Token Bus, Polling).

### Channel Partitioning (Slides 63-66)

- **FDMA (Frequency Division Multiple Access)**: Channel spectrum divided into frequency bands. Each station assigned a fixed band.
- **TDMA (Time Division Multiple Access)**: Time divided into slots. Each station assigned fixed slot(s) in a cycle.
- **CDMA (Code Division Multiple Access)**: Assigns unique "code" to each user. All users transmit simultaneously in the same frequency band. Receiver uses the code to isolate the desired sender's signal. Codes are orthogonal. Allows frequency reuse in adjacent cells (advantage in mobile networks). (Diagram on slide 66 illustrates mixing and recovery using codes).

### Random Access Protocols (Slides 68-75)

- **Pure Aloha**:
  - Transmit immediately when data arrives.
  - If collision occurs (sender detects interference from others), stop, wait a random time, retransmit.
  - Simple but high collision probability. Vulnerable period = 2 \* frame transmission time.
- **Slotted Aloha**:
  - Time divided into equal slots. Transmit only at the beginning of a slot.
  - Requires synchronization.
  - Reduces collision probability. Vulnerable period = 1 \* frame transmission time.
- **CSMA (Carrier Sense Multiple Access)**:
  - "Listen before talk".
  - If channel sensed idle, transmit entire frame.
  - If channel sensed busy, defer transmission (various strategies: 1-persistent, non-persistent, p-persistent).
  - Collisions still possible due to propagation delay (two nodes may sense idle simultaneously and start transmitting). (Diagram on slide 72 illustrates collision due to propagation delay).
- **CSMA/CD (Collision Detection)**:
  - Used in classic Ethernet (IEEE 802.3).
  - "Listen while talk".
  - Sense channel: if idle, transmit.
  - While transmitting, continue listening:
    - If collision detected: Abort transmission immediately, send a brief jam signal, wait a random backoff time (using Binary Exponential Backoff algorithm), retry sensing.
    - If transmission completes without collision: Done.
  - More efficient than CSMA as it aborts collided frames early.
- **CSMA/CA (Collision Avoidance)**:
  - Used in WiFi (IEEE 802.11). CD is difficult/impossible in wireless.
  - Listen before talk.
  - If channel idle for a period (DIFS), transmit.
  - If channel busy, wait until idle, then wait an _additional_ random backoff time before transmitting (avoids multiple waiting stations colliding).
  - Uses link-layer ACKs for confirmation. If ACK not received, assume collision/loss and increase backoff window before retry.
  - Optional RTS/CTS mechanism (see 802.11 section).
- **Comparison**: Channel partitioning is efficient and fair under heavy, uniform load but wasteful otherwise. Random access is efficient at low load but suffers from collisions at high load. Taking turns is a compromise.

### "Taking Turns" MAC Protocols (Slides 76-77)

- **Polling**:
  - Master node invites slave nodes to transmit in turn.
  - Used with "dumb" devices.
  - Concerns: Polling overhead, latency, single point of failure (master).
- **Token Passing (Token Ring/Token Bus)**:
  - A special "token" frame circulates.
  - Node must capture the token before transmitting.
  - After transmission, node passes the token to the next node.
  - Concerns: Token overhead, latency, complex recovery from lost token or node failure.

## LAN Devices: Hub, Switch, Bridge (Slides 79-83)

- **Repeater**: Physical layer (L1). Regenerates signal. Extends LAN segment length. Single collision domain.
- **Hub**: Physical layer (L1). Multi-port repeater. Receives signal on one port, regenerates, sends out all other ports. Single collision domain. Simple, inexpensive (historically).
- **Bridge**: Data Link layer (L2). Connects two LAN segments. Stores and forwards frames based on destination MAC address. Learns MAC addresses on each segment. Creates separate collision domains for each segment. Filters traffic (doesn't forward frames unnecessarily between segments).
- **Switch**: Data Link layer (L2). Multi-port bridge. Each port connects to a host or another switch/hub. Learns MAC addresses per port (self-learning). Forwards frames only to the destination port. Each port is its own collision domain. Allows multiple simultaneous transmissions between different port pairs.

## Switches: Operation & Comparison (Slides 84-95)

- **Switch Operation**:
  - Hosts have dedicated connections to switch ports.
  - Switches buffer incoming frames.
  - Uses Ethernet protocol on each link (often full-duplex).
  - **No collisions** on links to hosts in switched Ethernet. Each link is its own collision domain.
  - Allows simultaneous transmissions (e.g., A-to-A' and B-to-B' simultaneously, but not A-to-A' and C-to-A' if A' is on the same port).
- **Switch Forwarding Table (MAC Table)**:
  - Stores entries: (MAC address of host, Interface/Port to reach host, Time stamp/TTL).
  - Learned dynamically.
- **Switch Self-Learning**:
  - When a frame arrives on a port, the switch inspects the _source_ MAC address.
  - It adds/updates an entry in its table: (Source MAC, Incoming Port, TTL).
- **Switch Frame Filtering/Forwarding**:
  1.  Record source MAC and incoming port (learning step).
  2.  Look up the _destination_ MAC address in the table.
  3.  **If entry found**:
      - If destination port is the _same_ as the incoming port -> Filter (drop the frame, destination is on same segment).
      - If destination port is _different_ -> Forward the frame only out the specified destination port.
  4.  **If entry NOT found**: Flood -> Forward the frame out _all_ ports _except_ the incoming port.
- **Interconnecting Switches**: Self-learning works across multiple switches. Switches learn paths to MAC addresses through other switches via the flooding mechanism.
- **Switches vs. Routers**:
  - Both are store-and-forward devices.
  - **Switches**: Layer 2, use MAC addresses, learn tables via flooding/learning, operate within a broadcast domain (typically).
  - **Routers**: Layer 3, use IP addresses, compute tables using routing algorithms, connect different broadcast domains/networks.
- **Router Connecting LANs (Example Slides 94-95)**:
  - Host A wants to send to Host B (on different subnet via Router R).
  - A creates IP packet (Src IP: A, Dst IP: B).
  - A needs MAC address of _next hop_ (Router R's interface). Uses ARP if needed.
  - A creates Ethernet frame (Src MAC: A, Dst MAC: R).
  - Frame travels through switches to R.
  - Router R receives frame, removes Ethernet header, processes IP packet.
  - R determines next hop for B (directly connected or another router). Let's say B is on another interface of R.
  - R needs MAC address of B. Uses ARP if needed.
  - R creates _new_ Ethernet frame (Src MAC: R's outgoing interface, Dst MAC: B).
  - R forwards the new frame towards B.
  - **Key Point**: MAC addresses change at each L3 hop; IP addresses remain the same end-to-end.

## LAN Topologies & Standards (Slides 96-106)

- **LAN Topologies**: Traditional Bus, Ring, Star (Hub/Switch), WLAN (Wireless).
- **LAN Standards: IEEE 802.x**:
  - 802.1: Network Management
  - 802.2: Logical Link Control (LLC)
  - 802.3: Ethernet (CSMA/CD)
  - 802.4: Token Bus
  - 802.5: Token Ring
  - 802.11: Wireless LAN (WiFi)
  - 802.15: Wireless PAN (Bluetooth, ZigBee)
  - 802.16: WiMAX (Broadband Wireless Access)
  - ...and others.
- **LLC (IEEE 802.2)**:
  - **Role**: Interface between Network Layer protocols (IP, IPX, etc.) and various MAC layers. Multiplexing/Demultiplexing using DSAP/SSAP (Destination/Source Service Access Point) addresses.
  - **Functionalities**: Can provide 3 modes:
    1.  Unacknowledged connectionless (Type 1 - most common).
    2.  Acknowledged connectionless (Type 2).
    3.  Connection-oriented (Type 3).
  - **Frame Structure**: DSAP, SSAP, Control field.
    - Control field defines PDU type: U-frame (Unnumbered - connectionless), I-frame (Information - connection-oriented/ACKed), S-frame (Supervisory - control).
  - **Practical Use**: Often only Type 1 (U-frames) is used, as higher layers (like TCP) provide reliability and flow control.
- **Ethernet LAN (IEEE 802.3)**:
  - Invented 1976 (Metcalfe's sketch shown). Standardized as 802.3.
  - Speeds: 3 Mbps up to 10 Gbps and beyond (10BaseT, 100BaseT, 1000BaseT, etc.).
  - **Structure**: Datalink = LLC + MAC. Physical layer varies (copper, fiber).
  - **Classic MAC**: CSMA/CD.
  - **Ethernet Frame (DIX/Ethernet II format shown on slide 105)**:
    - Preamble (7 bytes): Synchronization (alternating 1s and 0s).
    - SFD (Start Frame Delimiter) (1 byte): 10101011.
    - Destination MAC (6 bytes).
    - Source MAC (6 bytes).
    - Type (2 bytes): Indicates upper layer protocol (e.g., 0x0800 for IP). (Note: IEEE 802.3 uses a Length field here instead for LLC PDU length).
    - Payload (Data + Padding): 46-1500 bytes. Padding added if data < 46 bytes.
    - CRC (Checksum) (4 bytes): Error detection for the frame.
- **Switched Ethernet**:
  - Dominant modern Ethernet. Star topology with central switch.
  - Switch forwards frames only to destination port.
  - No collisions (in full-duplex mode). CSMA/CD mechanism often disabled.

## Wireless LAN (IEEE 802.11) (Slides 107-120)

- **Components**: Base Station (Access Point - AP), Stations (clients with wireless NICs).
- **Modes**:
  - **Infrastructure Mode**: Stations connect to an AP. AP connects BSS (Basic Service Set) to wired network (Distribution System). Multiple BSSs can form an ESS (Extended Service Set).
  - **Ad hoc Mode**: Stations connect directly peer-to-peer without an AP.
- **Standards**:
  - 802.11b: 2.4 GHz, max 11 Mbps.
  - 802.11a: 5 GHz, max 54 Mbps.
  - 802.11g: 2.4 GHz, max 54 Mbps.
  - 802.11n: 2.4/5 GHz, uses MIMO (Multiple Input Multiple Output antennas), max ~200+ Mbps.
  - (Newer: 802.11ac, 802.11ax/WiFi 6/6E).
- **MAC**: Uses CSMA/CA.
- **Channel and Connection**:
  - Spectrum divided into channels (e.g., 11/13/14 channels in 2.4 GHz band, spaced 5 MHz apart, but wider signals overlap). Admin/AP chooses operating channel.
  - **Association**: Station needs to connect to an AP.
    - **Passive Scanning**: Station listens for Beacon frames periodically sent by APs (contain AP's SSID, MAC, capabilities).
    - **Active Scanning**: Station broadcasts Probe Request frames. APs within range reply with Probe Response frames.
    - Station selects an AP and sends Association Request.
    - AP replies with Association Response.
- **CSMA/CA in 802.11**:
  - Collision Detection (CD) is hard in wireless (can't easily listen while sending, hidden terminal problem, fading).
  - **Hidden Terminal Problem**: Station A and C can both hear AP B, but not each other. They might transmit to B simultaneously causing a collision at B, which A and C cannot detect themselves.
  - **Mechanism**:
    1.  Sense channel. If idle for DIFS (Distributed Inter-Frame Space), transmit frame.
    2.  If channel busy, wait until idle, then wait a random backoff time before transmitting. Backoff timer only counts down when channel is idle.
    3.  Receiver, upon correct reception, waits SIFS (Short IFS - shorter than DIFS) and sends an ACK.
    4.  If sender doesn't receive ACK (timeout), assume collision/loss, increase contention window (binary exponential backoff), and retry.
- **Collision Avoidance using RTS/CTS (Optional)**:
  - Addresses hidden terminal problem, useful for large frames.
  - Sender sends short RTS (Request To Send) frame using CSMA/CA.
  - AP (or destination) replies with short CTS (Clear To Send) frame.
  - All stations hearing CTS (even if they didn't hear RTS) defer access for the duration specified in CTS (Network Allocation Vector - NAV).
  - Sender then transmits data frame, followed by ACK.
  - RTS/CTS adds overhead but reduces chance of collision for the larger data frame.
- **802.11 Frame Structure**:
  - More complex than Ethernet. Fields include:
    - **Frame Control (2 bytes)**: Protocol version, Type (Mgmt, Control, Data), Subtype, flags (To AP, From AP, More Frag, Retry, Power Mgt, More Data, WEP, Order).
    - **Duration/ID (2 bytes)**: Time to reserve channel (NAV) for RTS/CTS/Data/ACK, or Association ID (AID).
    - **Address 1 (6 bytes)**: Receiver Address (RA) - typically destination MAC.
    - **Address 2 (6 bytes)**: Transmitter Address (TA) - typically source MAC.
    - **Address 3 (6 bytes)**: Depends on context (e.g., BSSID (AP MAC) in infrastructure mode, or original Dest/Source in WDS). Used for filtering by AP.
    - **Sequence Control (2 bytes)**: Fragment number, Sequence number.
    - **Address 4 (6 bytes)**: Optional, used in Wireless Distribution System (WDS) bridge mode.
    - **Payload (0-2312 bytes)**: Data.
    - **CRC (4 bytes)**: Frame Check Sequence.
  - **Addressing Example (Infrastructure Mode - H1 to Router R1 via AP)**:
    - Frame H1 -> AP: Addr1=AP MAC, Addr2=H1 MAC, Addr3=R1 MAC.
    - AP forwards to wired network: Creates Ethernet frame (Dst=R1 MAC, Src=AP MAC - or H1 MAC if bridging).

## Virtual LANs (VLANs) (Slides 121-127)

- **Motivation**:
  - **Scalability**: Single large broadcast domain is inefficient (ARP, DHCP floods). VLANs create smaller broadcast domains.
  - **Security/Privacy**: Isolate traffic between different groups/departments.
  - **Administrative Flexibility**: Group users logically (e.g., by department) regardless of physical location/switch connection. Move user -> change port VLAN config, not physical wiring.
- **Concept**: Partition a single physical LAN infrastructure (switches) into multiple logical LANs. Traffic within a VLAN is isolated at Layer 2 (broadcasts contained). Forwarding _between_ VLANs requires a Layer 3 device (router).
- **Port-Based VLANs**:
  - Switch ports are manually configured as members of a specific VLAN (e.g., ports 1-8 = VLAN 10, ports 9-15 = VLAN 20).
  - A physical switch operates as multiple virtual switches.
  - Traffic entering port 1 (VLAN 10) can only be forwarded out other ports belonging to VLAN 10 on that switch (unless forwarded to a router).
- **VLANs Spanning Multiple Switches**:
  - Need a way to connect switches while maintaining VLAN separation.
  - **Trunk Port**: A switch port configured to carry traffic for _multiple_ VLANs between switches (or switch-router).
  - **VLAN Tagging (IEEE 802.1Q)**: Protocol used on trunk ports.
    - Adds a 4-byte tag _inside_ the Ethernet frame (between Source MAC and Type/Length fields).
    - **Tag Format**:
      - TPID (Tag Protocol Identifier - 2 bytes): Value 0x8100 indicates an 802.1Q tagged frame.
      - TCI (Tag Control Information - 2 bytes):
        - Priority Code Point (PCP - 3 bits): QoS priority.
        - Drop Eligible Indicator (DEI - 1 bit): Formerly CFI.
        - VLAN ID (VID - 12 bits): Identifies the VLAN (1-4094).
    - Original CRC is recalculated to include the tag.
    - Switches add tags when frames go out a trunk port, and remove tags when frames go out an access port (a port belonging to a single VLAN).

## Access Networks & Optical Fiber (Slides 128-140)

- **Access Network**: Connects end-users/subscribers to the core network/ISP. Gathers data from users. Examples: PSTN, Cable TV network, Internet-to-home.
- **Architecture Components**:
  - **Hub/Headend/CO (Central Office)**: Service provider side equipment.
  - **NIU (Network Interface Unit)** / **NID (Network Interface Device)**: Device at user premises.
  - **RN (Remote Node)** / **Distribution Node**: Intermediate point distributing signals (e.g., DSLAM, Cable CMTS node, PON Splitter).
- **Wired Access Technologies**:
  - **Dial-up**: ~56 kbps max, uses voice frequency band on phone line (obsolete).
  - **ADSL (Asymmetric Digital Subscriber Line)**: Few Mbps, uses phone line, higher frequencies than voice (allows simultaneous voice/data). Popular 2000-2010s.
  - **HFC (Hybrid Fiber Coax)**: Uses Cable TV infrastructure. Fiber to neighborhood nodes, coax from node to homes. Shared bandwidth (upstream especially).
  - **FTTx (Fiber To The x)**: Uses optical fiber. Popular now. Dozens/hundreds Mbps or Gbps.
- **FTTx Architectures**: Defined by how close fiber gets to the user:
  - **FTTCab (Fiber To The Cabinet)**: Fiber ends at a street cabinet (< 1km from user), copper (e.g., VDSL) continues.
  - **FTTC (Fiber To The Curb) / FTTB (Fiber To The Building)**: Fiber ends closer (e.g., basement FTTB, pole FTTC), serving fewer users (< 100m copper). ONU serves multiple subscribers.
  - **FTTH (Fiber To The Home)**: Fiber runs all the way to the individual home/unit. ONU/ONT acts as NIU.
- **AON vs. PON**: Two main optical access network types:
  - **AON (Active Optical Network)**: Remote Nodes (distribution points) are _active_ electronic devices (switches/routers) requiring power. Can support longer distances (~100 km). Point-to-point links from RN to users.
  - **PON (Passive Optical Network)**: Remote Nodes are _passive_ optical splitters requiring no power. Lower cost/maintenance. Shorter distance limit (~20 km). Point-to-multipoint architecture. Dominant for FTTH.
    - **Downstream**: OLT (Optical Line Terminal at CO) broadcasts signal. Splitter divides power. ONU/ONT receives all signals but only processes data addressed to it (based on ID/timeslot).
    - **Upstream**: ONUs share the single fiber back to the OLT. Requires MAC protocol (usually TDMA). OLT grants transmission time slots to each ONU to avoid collisions.
- **PON Standards**:
  - **EPON (Ethernet PON)** (IEEE 802.3ah): Transports standard Ethernet frames over PON. Common in Asia. Typically 1 Gbps shared.
  - **GPON (Gigabit PON)** (ITU-T G.984): Can carry multiple traffic types (Ethernet, ATM, TDM voice) using GPON Encapsulation Method (GEM). Higher bandwidth (e.g., 2.5 Gbps downstream / 1.25 Gbps upstream shared). More common globally. Data encapsulated in GPON frames.
  - **WPON (WDM PON)**: Uses different wavelengths (colors of light) for different ONUs/services. Effectively gives each ONU a dedicated point-to-point connection. Not yet widely standardized or deployed. Remote node is an AWG (Arrayed Waveguide Grating) - passive wavelength MUX/DEMUX.

---
