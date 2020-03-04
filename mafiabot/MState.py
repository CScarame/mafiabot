from typing import Dict , List, Optional, Callable, Tuple
import json

from .MPlayer import *
from .MEvent import MEvent, MEventType, MEventC
from .MEx import MPlayerID, NOTARGET
from .MResp import MResp, MRespType

# Phases
INIT = 'Init'
DAY = "Day"
NIGHT = "Night"

def createPlayers(playerids : List[MPlayerID]) -> Dict[MPlayerID, MPlayer]:
  ## Gen roles using the number of players (and rules?)
  # Then randomly assign a role to each player and create player object
  # TODO: encapsulate

  num_players = len(playerids)

  DEFAULT_ROLE_LIST = [
    "TOWN", "TOWN", "MAFIA",
    "COP", "DOCTOR", "CELEB", "MILLER",
    "GODFATHER", "STRIPPER", "MILKY",
    "IDIOT", "SURVIVOR", "TOWN", "TOWN",
    "GOON", "MASON", "MASON",
    "GUARD", "AGENT",
  ]

  default_roles = DEFAULT_ROLE_LIST[:num_players]

  players = {}
  for playerid, role in zip(playerids, default_roles):
    players[playerid] = MPlayer(playerid, role)

  for player in players.values():
    if player.role in CONTRACT_ROLES:
      if player.role in ['IDIOT', 'SURVIVOR']:
        player.target = player.id
      else:
        player.target = playerids[0]

  return players


class MState:
  """State and handlers for Mafia game"""

  @staticmethod
  def fromPlayers(
    players : List[MPlayerID], 
    rolegen : Callable[[List[MPlayerID]], Dict[MPlayerID, MPlayer]] = createPlayers,
    mresp:MResp = MResp()
    ):
    mstate = MState()

    mstate.mresp : Callable[..., None] = mresp.resp # handle to responding object

    mstate.day = 0
    mstate.phase = INIT # Init|Day|Night

    mstate.players : Dict[MPlayerID, MPlayer] = rolegen(players)
    mstate.contracts : Dict[MPlayerID,Tuple[MPlayerID, str]] = {}
    for p in mstate.players.values():
      if p.role in CONTRACT_ROLES:
        mstate.contracts[p.id] = (p.target, p.role)

    mstate.mafia_targeter : Optional[MPlayerID] = None
    mstate.mafia_target : Optional[MPlayerID] = None

    mstate.handleEvent(MEventC.start())

    return mstate

  @staticmethod
  def fromJSON(json_str : str,
    mresp:MResp = MResp()):
    mstate = MState()

    json_dict = json.loads(json_str)

    players = [MPlayer(**p) for p in json_dict['players']]
    del(json_dict['players'])

    mstate.__dict__ = json_dict

    mstate.players : Dict[MPlayerID, MPlayer] = {}
    for p in players:
      mstate.players[p.id] = p

    mstate.mresp : Callable[..., None] = mresp.resp
    return mstate

  def vote(self, voter : MPlayerID, votee : Optional[MPlayerID]):
    assert(voter in self.players)
    assert(votee == None or votee == NOTARGET or votee in self.players)
    assert(self.phase == DAY)
    self.handleEvent(MEventC.vote(voter, votee))

  def mtarget(self, killer : MPlayerID, target : Optional[MPlayerID]):
    assert(killer in self.players and self.players[killer].role in MAFIA_ROLES)
    assert(target == None or target == NOTARGET or target in self.players)
    assert(self.phase == NIGHT)
    self.handleEvent(MEventC.mtarget(killer, target))

  def target(self, player : MPlayerID, target : Optional[MPlayerID]):
    assert(player in self.players and self.players[player].role in TARGETING_ROLES)
    assert(target == None or target == NOTARGET or target in self.players)
    assert(self.phase == NIGHT)
    self.handleEvent(MEventC.target(player, target))

  def reveal(self, player : MPlayerID):
    assert(player in self.players and self.players[player].role == 'CELEB')
    assert(self.phase == DAY)
    self.handleEvent(MEventC.reveal(player))

  def timer(self):
    # Assertions in the future?
    self.handleEvent(MEventC.timer())

  ## Methods after this should be hidden?

  def handleEvent(self, event : MEvent):
    # TODO: Do logging in the switch below (Except Day, handle that later?)

    next_event : Optional[MEvent] = None

    if event.type == MEventType.VOTE:
      self.players[event.voter].vote = event.votee  
      next_event = self.checkVotes(event)

    elif event.type == MEventType.MTARGET:
      self.mresp(MRespType.MTARGET, **event.data)
      self.mafia_target = event.target
      self.mafia_targeter = event.actor
      next_event = self.checkNightTargets()

    elif event.type == MEventType.TARGET:
      self.mresp(MRespType.TARGET, **event.data)
      self.players[event.actor].target = event.target
      next_event = self.checkNightTargets()

    elif event.type == MEventType.REVEAL:
      
      self.mresp(MRespType.REVEAL, **event.data)

    elif event.type == MEventType.TIMER:
      if self.phase == DAY:
        self.mresp(MRespType.TIMER_DAY, **event.data)
        next_event = MEventC.night()
      if self.phase == NIGHT:
        self.mresp(MRespType.TIMER_NIGHT, **event.data)
        next_event = MEventC.day()

    # End of external events
    elif event.type == MEventType.START:
      self.mresp(MRespType.START, players = self.players)

      self.day = 1
      self.phase = DAY
      self.state = "Play"

    elif event.type == MEventType.ELECT:
      self.mresp(MRespType.ELECT, **event.data)
      if event.target != None and event.target != NOTARGET:
        self.eliminate(event.target, event.actor)
        next_event = self.checkWin()
      if next_event == None:
        next_event = MEventC.night()

    elif event.type == MEventType.KILL:
      self.mresp(MRespType.KILL, **(event.data))
      if event.success:
        if not event.target == NOTARGET: # Target should never be NOTARGET anyway
          self.eliminate(event.target, event.actor)
          next_event = self.checkWin()

    elif event.type == MEventType.STRIP:
      self.mresp(MRespType.STRIP, **event.data)
    elif event.type == MEventType.SAVE:
      self.mresp(MRespType.SAVE, **event.data)
    elif event.type == MEventType.MILK:
      self.mresp(MRespType.MILK, **event.data)
    elif event.type == MEventType.INVESTIGATE:
      self.mresp(MRespType.INVESTIGATE, **(event.data))

    elif event.type == MEventType.DAY:
      self.toDay()
      self.resetPlayers()
      self.mresp(MRespType.DAY, **event.data)
      #Start of DAY logging?

    elif event.type == MEventType.NIGHT:
      self.mresp(MRespType.NIGHT, **event.data)
      self.resetPlayers()
      self.phase = NIGHT

    elif event.type == MEventType.TOWN_WIN:
      # logging
      self.mresp(MRespType.TOWN_WIN, **event.data)
      self.checkContractWins()
      raise TownWinException()

    elif event.type == MEventType.MAFIA_WIN:
      # logging
      self.mresp(MRespType.MAFIA_WIN, **event.data)
      self.checkContractWins()
      raise MafiaWinException()

    elif event.type == MEventType.CHARGE_DIE:
      # logging
      needed_alive = event.role in ['GUARD', 'SURVIVOR']

      if event.player == event.charge:
        self.mresp(MRespType.SURVIVOR_IDIOT_DIE, **event.data)
      else:
        if event.charge == event.aggressor or event.player == event.aggressor:
          # Target killed by self or player, become SURVIVOR/IDIOT
          new_charge = event.player
          new_role = 'IDIOT' if needed_alive else 'SURVIVOR'
          event.data['new_role'] = new_role
          self.mresp(MRespType.CHARGE_REFOCUS_SELF, **event.data)
        else: # otherwise, aggressor MUST be alive
          new_charge = event.aggressor
          new_role = 'AGENT' if needed_alive else 'GUARD'
          event.data['new_role'] = new_role
          self.mresp(MRespType.CHARGE_REFOCUS, **event.data)
        player = self.players[event.player]
        player.role = new_role
        player.target = new_charge
        self.contracts[event.player] = (new_charge, new_role)

    elif event.type == MEventType.CONTRACT_RESULT:
      if event.success:
        self.mresp(MRespType.CONTRACT_WIN, **event.data)
      else:
        self.mresp(MRespType.CONTRACT_LOSE, **event.data)


    else:
      raise NotImplementedError((event.type.name)+ ": " + str(event.data))

    if next_event != None:
      return self.handleEvent(next_event)

    # TODO: update save data
    return

  def checkVotes(self, vote_event : MEvent) -> Optional[MEvent]:
    assert(vote_event.type == MEventType.VOTE)
    p : Optional[MPlayerID, None] = vote_event.votee
    players : Dict[MPlayerID, MPlayer] = self.players

    if p == None:
      self.mresp(MRespType.VOTE_RETRACT, **(vote_event.data))
      return
    num_voters = len([v for v in players if players[v].vote == p])
    num_players = len(players)
    thresh = int(len(players)/2) + 1
    no_kill_thresh = num_players - thresh + 1

    if p == NOTARGET:
      vote_event.data['remain'] = no_kill_thresh-num_voters
      self.mresp(MRespType.VOTE_NOKILL, **(vote_event.data))
      if num_voters >= no_kill_thresh:
        return MEventC.elect(vote_event.voter, vote_event.votee) # Add last words timer
    else: # Vote for Player
      vote_event.data['remain'] = thresh-num_voters
      self.mresp(MRespType.VOTE_PLAYER, **(vote_event.data))
      if num_voters >= thresh:
        return MEventC.elect(vote_event.voter, vote_event.votee) # Add last words timer
    return None

  def checkNightTargets(self) -> Optional[MEvent]:
    players : Dict[MPlayerID, MPlayer] = self.players
    mtarget : Optional[MPlayerID] = self.mafia_target
    if mtarget == None:
      return
    if any([t.target == None for t in players.values() if t.role in TARGETING_ROLES]):
      return
    ## All targets have been selected, do to day stuff?
    return MEventC.day()


  def checkWin(self) -> Optional[MEvent]:
    players : Dict[MPlayerID, MPlayer] = self.players
    num_players = len(players)
    num_mafia = len([m for m in players.values() if m.role in MAFIA_ROLES])
    
    if num_mafia == 0:
      return MEventC.town_win()
    elif num_mafia >= num_players/2:
      return MEventC.mafia_win()
    return None

  def eliminate(self, target : MPlayerID, actor : MPlayerID):
    # if target IS actor, special case for AGENTs and GUARDs
    # for GUARD: lose? (SURVIVOR case covered?)
    #  Cannot refocus. Become IDIOT!
    # for AGENT: win? (IDIOT case covered?)
    #  Cannot refocus. Become SURVIVOR!
    del(self.players[target])
    self.checkCharges(target, actor) 

  def checkCharges(self, killed : MPlayerID, aggressor : MPlayerID):
    for (p_id, (p_charge, p_role)) in self.contracts.items():
      if p_charge == killed: # This is the case we are worried about
        self.handleEvent(MEventC.charge_die(p_id, p_charge, p_role, aggressor))

  def checkContractWins(self):
    for (p_id, (p_charge, p_role)) in self.contracts.items():
      needed_alive = p_role in ['GUARD', 'SURVIVOR']
      charge_alive = p_charge in self.players
      success = needed_alive == charge_alive
      self.handleEvent(MEventC.contract_result(p_id, p_charge, p_role, success))

  def toDay(self) -> None:
    # check stripper blocks
    blocked_ids = []
    to_kill : Optional[MPlayerID] = None
    for stripper_id in [p for p in self.players if self.players[p].role == "STRIPPER"]:
      stripper = self.players[stripper_id]
      if stripper.target == None:
        stripper.target = NOTARGET
      if not stripper.target == NOTARGET:
        blocked_ids.append(stripper.target)
        useful = self.players[stripper.target].role in TARGETING_ROLES
        self.handleEvent(MEventC.strip(stripper_id, stripper.target, useful)) # Even will check for success and log

    # try mafia kill (doctor can save)
    if self.mafia_target == None:
      self.mafia_target = NOTARGET
    
    target_saved = False
    if not self.mafia_target == NOTARGET:
      # target is real
      for doctor_id in [p for p in self.players if self.players[p].role == "DOCTOR"]:
        doctor = self.players[doctor_id]
        if doctor.target == None:
          doctor.target = NOTARGET
        if not doctor.target == NOTARGET:
          successful = doctor.target == self.mafia_target
          blocked = doctor_id in blocked_ids
          if successful:
            if blocked:
              pass
            else:
              target_saved = True
          self.handleEvent(MEventC.save(doctor_id, doctor.target, blocked, successful))
      # Now kill proceeds
      if not target_saved:
        to_kill = self.mafia_target
      self.handleEvent(MEventC.kill(self.mafia_targeter, self.mafia_target, not target_saved))

    # milky gives milk
    for milky_id in [p for p in self.players if self.players[p].role == "MILKY"]:
      milky = self.players[milky_id]
      if milky.target == None:
        milky.target = NOTARGET
      if not milky.target == NOTARGET:
        blocked = milky_id in blocked_ids
        sniped = milky.target == to_kill and not target_saved
        self.handleEvent(MEventC.milk(milky_id, milky.target, blocked, sniped))
    
    # Cop investigates
    for cop_id in [p for p in self.players if self.players[p].role == "COP"]:
      cop = self.players[cop_id]
      if cop.target == None:
        cop.target = NOTARGET
      if not cop.target == NOTARGET:
        blocked = cop_id in blocked_ids
        sniped = cop.target == to_kill and not target_saved
        self.handleEvent(MEventC.investigate(cop_id, cop.target, blocked, sniped))
    
    # Finally switch to day
    self.day += 1
    self.phase = DAY

  def resetPlayers(self):
    self.mafia_target = None
    self.mafia_targeter = None
    for p in self.players.values():
      p.vote = None
      if not p.role in CONTRACT_ROLES:
        p.target = None

  def toJSON(self) -> str:
    ## Creates a json string from the game
    json_dict = self.__dict__
    del(json_dict['mresp'])
    json_dict['players'] = [p.__dict__ for p in self.players.values()]

    return json.dumps(json_dict)

class EndGameException(Exception):
  pass

class TownWinException(EndGameException):
  pass

class MafiaWinException(EndGameException):
  pass