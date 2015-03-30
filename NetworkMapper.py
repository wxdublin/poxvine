'''
Virtual Network Simulator POX Application.
Author : Kausik Subramanian
'''

from pox.core import core
from collections import defaultdict

import pox.openflow.libopenflow_01 as of
import pox.lib.packet as pkt
import pox.openflow.discovery
import pox.openflow.spanning_tree
from pox.lib.packet.ethernet import ethernet
from pox.lib.packet.vlan import vlan
from pox.lib.packet.ipv4 import ipv4 
from pox.lib.revent import *
from pox.lib.util import dpid_to_str
from pox.lib.util import dpidToStr

from pox.lib.addresses import IPAddr, EthAddr
from collections import namedtuple
import os 
from NetworkMapping import Route 

log = core.getLogger()


class NetworkMapper (EventMixin):

	def __init__(self):
		self.listenTo(core.openflow)
		core.openflow_discovery.addListeners(self)
		log.debug("Enabling NetworkMapper Module")

		# Adjacency map.  [sw1][sw2] -> port from sw1 to sw2
		self.adjacency = defaultdict(lambda:defaultdict(lambda:None))
		
		self.switchMap = dict()
		self.switchConnections = dict()


		#Temp
		self.routeAdded = False

		
	"""This event will be raised each time a switch will connect to the controller"""
	def _handle_ConnectionUp(self, event):
		
		# Use dpid to differentiate between switches (datapath-id)
		# Each switch has its own flow table. As we'll see in this 
		# example we need to write different rules in different tables.
		dpid = dpidToStr(event.dpid)
		log.debug("Switch %s has come up.", dpid)
		
		switchName = ""
		for m in event.connection.features.ports:
			name = m.name.split("-")
			if switchName == "" :
				switchName = name[0]
			if not switchName == name[0] :
				log.debug("Some Error in mapping name from the OpenFlow Switch Up Message.")

		self.switchMap[switchName] = dpid
		self.switchConnections[switchName] = event.connection


	def findSwitchName(self, dpid) :
		for name in self.switchMap.iterkeys() :
			if self.switchMap[name] == dpid :
				return name

	def findOutputPort(self, swName, srcip="0.0.0.0", dstip="0.0.0.0", tenantID = 0, routeTag = 0) :
		neighbour = ""
		if swName == "s1" and srcip == "10.0.0.5" and dstip == "10.1.0.5":
			neighbour = "s2"
		if swName == "s2" and srcip == "10.0.0.5" and dstip == "10.1.0.5":
			neighbour = "s3"
		if swName == "s3" and srcip == "10.0.0.5" and dstip == "10.1.0.5":
			neighbour = "s4"
		if swName == "s4" and srcip == "10.0.0.5" and dstip == "10.1.0.5":
			return of.OFPP_FLOOD
		if swName == "s4" and srcip == "10.1.0.5" and dstip == "10.0.0.5":
			neighbour = "s3"
		if swName == "s3" and srcip == "10.1.0.5" and dstip == "10.0.0.5":
			neighbour = "s2"
		if swName == "s2" and srcip == "10.1.0.5" and dstip == "10.0.0.5":
			neighbour = "s1"
		if swName == "s1" and srcip == "10.1.0.5" and dstip == "10.0.0.5":
			return of.OFPP_FLOOD

		if not self.adjacency[self.switchMap[swName]][self.switchMap[neighbour]] == None :
			return self.adjacency[self.switchMap[swName]][self.switchMap[neighbour]]
		else :
			return None

	def findOutputPort1(self, curr, next, prev = None) :
		if next == prev :
			return of.OFPP_IN_PORT # send back on the input port.

		elif not self.adjacency[self.switchMap[curr]][self.switchMap[next]] == None :
			return self.adjacency[self.switchMap[curr]][self.switchMap[next]]
		
		else :
			return None


	def getSubnet(self, ip, subnetMask) :
		if ip == "10.0.0.5" or ip == "10.0.0.0":
			return "10.0.0.0"
		if ip == "10.1.0.5" or ip == "10.1.0.0":
			return "10.1.0.0"


	def addForwardingRules(self, srcSubnet, dstSubnet, route) :
		"This function proactively adds the forwarding rules from srcSubnet to dstSubnet."

		currRouteTag = 2 # Start with 1. 

		# First switch
		sw = route.getFirstSwitch()
		sw_next = route.getNextSwitch()
		print "Adding VLAN Tag rule for Switch " + sw
		self.proactiveInstallRule(
			connection = self.switchConnections[sw], 
			srcip = srcSubnet, dstip = dstSubnet, 
			outport = self.findOutputPort1(curr = sw, next = sw_next, prev = None), 
			vlanMatch = 0, vlanAction = 4,
			routeTagMatch = 0, routeTagAction = currRouteTag)

		sw_prev = sw
		sw = route.getCurrentSwitch()
		while not route.isLastSwitch() : 
			sw_next = route.getNextSwitch()

			print "Adding rule for Switch " + sw
			if route.getCurrentRouteTag() :
				self.proactiveInstallRule(
					connection = self.switchConnections[sw], 
					srcip = srcSubnet, dstip = dstSubnet, 
					outport = self.findOutputPort1(curr = sw, next = sw_next, prev = sw_prev),
					vlanMatch = 4, vlanAction = 0 ,
					routeTagMatch = currRouteTag, routeTagAction = (currRouteTag + 1) ) 
				currRouteTag += 1
			else : 
				self.proactiveInstallRule(
					connection = self.switchConnections[sw], 
					srcip = srcSubnet, dstip = dstSubnet, 
					outport = self.findOutputPort1(curr = sw, next = sw_next, prev = sw_prev),
					vlanMatch = 4, vlanAction = 0,
					routeTagMatch = currRouteTag, routeTagAction = 0)

			sw_prev = sw
			sw = route.getCurrentSwitch()


		# Last Switch. Strip VLAN
		print "Adding rule for Switch " +  sw

		self.proactiveInstallRule(
			connection = self.switchConnections[sw], 
			srcip = srcSubnet, dstip = dstSubnet, 
			outport = of.OFPP_FLOOD, 
			vlanMatch = 4 , vlanAction = -1,
			routeTagMatch = currRouteTag, routeTagAction = 0)
	

	def getVlanId(self, tenantID, routeTag) :
		# A function of tenant ID and routeTag. 
		# 12 Bit VLAN ID: Most Significant 6 bits : tenantID, Least Significant 6 bits : routeTag
		return (tenantID * 64 + routeTag)

	def proactiveInstallRule(self, connection, srcip, dstip, outport, 
		vlanMatch = 0, vlanAction = 0, routeTagMatch = 0, routeTagAction = 0):
		msg = of.ofp_flow_mod()
		
		#Match 
		msg.match = of.ofp_match()
		msg.match.dl_type = ethernet.IP_TYPE
		msg.match.set_nw_src(IPAddr(self.getSubnet(srcip, 24)), 24)
		msg.match.set_nw_dst(IPAddr(self.getSubnet(dstip, 24)), 24)

		if not vlanMatch == 0 and not routeTagMatch == 0 : 
			msg.match.dl_vlan = self.getVlanId(vlanMatch, routeTagMatch)

		if vlanAction == -1 : 
			#Strip Vlan tag.
			msg.actions.append(of.ofp_action_strip_vlan())

		elif not vlanAction == 0 and not routeTagAction == 0: 
			# Need to set VLAN Tag for isolation of tenant traffic.
			msg.actions.append(of.ofp_action_vlan_vid(vlan_vid = self.getVlanId(vlanAction, routeTagAction))) 
		
		elif vlanAction == 0 and not routeTagAction == 0:
			msg.actions.append(of.ofp_action_vlan_vid(vlan_vid = self.getVlanId(vlanMatch, routeTagAction))) 


		msg.actions.append(of.ofp_action_output(port = outport))
		connection.send(msg)

	def reactiveInstallRule(self, event, srcip, dstip, outport, vlan=0):
		msg = of.ofp_flow_mod()
		
		#Match 
		msg.match = of.ofp_match()
		msg.match.dl_type = ethernet.IP_TYPE
		msg.match.set_nw_src(IPAddr(self.getSubnet(srcip, 24)), 24)
		msg.match.set_nw_dst(IPAddr(self.getSubnet(dstip, 24)), 24)

		"""
		if not vlan == 0 : 
			# Need to set VLAN Tag for isolation of tenant traffic.
			msg.actions.append(of.ofp_action_vlan_vid(vlan_vid = vlan)) """

		msg.actions.append(of.ofp_action_output(port = outport))

		msg.data = event.ofp
		msg.in_port = event.port
		event.connection.send(msg)

	def _handle_LinkEvent (self, event):
		l = event.link
		sw1 = dpid_to_str(l.dpid1)
		sw2 = dpid_to_str(l.dpid2)

		log.debug ("link %s[%d] <-> %s[%d]",
				   sw1, l.port1,
				   sw2, l.port2)

		self.adjacency[sw1][sw2] = int(l.port1)
		self.adjacency[sw2][sw1] = int(l.port2)

	def _handle_PacketIn (self, event):
		"""
		Handle packet in messages from the switch.
		"""
		packet = event.parsed


		def install_fwdrule(event,srcip,dstip,outport,vlan=0):
			msg = of.ofp_flow_mod()
			
			#Match 
			msg.match = of.ofp_match()
			msg.match.dl_type = ethernet.IP_TYPE
			msg.match.set_nw_src(IPAddr(self.getSubnet(srcip, 24)), 24)
			msg.match.set_nw_dst(IPAddr(self.getSubnet(dstip, 24)), 24)

			
			if not vlan == 0 : 
				# Need to set VLAN Tag for isolation of tenant traffic.
				msg.actions.append(of.ofp_action_vlan_vid(vlan_vid = vlan)) 

			msg.actions.append(of.ofp_action_output(port = outport))

			msg.data = event.ofp
			msg.in_port = event.port
			event.connection.send(msg)

		def installFloodRule(event,packet,outport,vlan=0):
			msg = of.ofp_flow_mod()
			
			#Match 
			msg.match = of.ofp_match.from_packet(packet, event.port)		
			msg.actions.append(of.ofp_action_output(port = outport))
			
			if not vlan == 0 : 
				# Need to set VLAN Tag for isolation of tenant traffic.
				msg.actions.append(of.ofp_action_vlan_vid(vlan_vid = vlan))


			msg.data = event.ofp
			msg.in_port = event.port
			event.connection.send(msg)
		
		def handle_IP_packet (packet):
			ip = packet.find('ipv4')
			if ip is None:
				# This packet isn't IP!
				print "packet type has no transport ports, flooding"
				installFloodRule(event,packet,of.OFPP_FLOOD)
				return

			else :
  				print "Source IP:", ip.srcip

  				vlanPacket = packet.find('ethernet')
  				routeTag = 0
  				if vlanPacket.type == ethernet.VLAN_TYPE :
  					print "Vlan header is there."
  					print packet.__str__()

  				route1 = Route()
  				route2 = Route()
  				if not self.routeAdded : 
	  				route1.addSrcSubnet("10.0.0.0", 24)
	  				route1.addDstSubnet("10.1.0.0", 24)
	  				route1.addNextSwitch("s1", False)
	  				route1.addNextSwitch("s2", False)
	  				route1.addNextSwitch("s3", False)
	  				route1.addNextSwitch("s4", True)
	  				route1.addNextSwitch("s3", True)
	  				route1.addNextSwitch("s4", False)
	  				"""
	  				route1.addNextSwitch("s3", False)
	  				route1.addNextSwitch("s2", False)
	  				route1.addNextSwitch("s1", True)
	  				route1.addNextSwitch("s2", False)
	  				route1.addNextSwitch("s3", False)
	  				route1.addNextSwitch("s4", False) """

					self.addForwardingRules(srcSubnet = "10.0.0.0" , dstSubnet = "10.1.0.0", 
					route = route1)


					route2.addSrcSubnet("10.1.0.0", 24)
	  				route2.addDstSubnet("10.0.0.0", 24)
	  				route2.addNextSwitch("s4", False)
	  				route2.addNextSwitch("s3", False)
	  				route2.addNextSwitch("s2", False)
	  				route2.addNextSwitch("s1", False)

	  				self.addForwardingRules(srcSubnet = "10.1.0.0" , dstSubnet = "10.0.0.0", 
					route = route2)


					self.routeAdded = True
	  				
  				#switch is event.dpid
  				"""
				sw = dpidToStr(event.dpid)
				swName = self.findSwitchName(sw)
				outport = self.findOutputPort(swName, ip.srcip, ip.dstip, 0, routeTag)
				install_fwdrule(event, ip.srcip, ip.dstip, outport, 5)
				"""
				


		handle_IP_packet(packet)

		# flood and install the flow table entry for the flood
		
		

def launch():
	# Run spanning tree so that we can deal with topologies with loops
	pox.openflow.discovery.launch()

	'''
	Starting the Topology Slicing module
	'''
	core.registerNew(NetworkMapper)
