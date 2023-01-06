import random
from collections import defaultdict
from functools import partial
from typing import Dict, List
from math import sqrt

from pathfinding.core.diagonal_movement import DiagonalMovement
from pathfinding.core.grid import Grid
from pathfinding.finder.a_star import AStarFinder

from gupb import controller
from gupb.model import arenas, coordinates, tiles, weapons
from gupb.model import characters
from gupb.model.arenas import Arena, Terrain
from gupb.model.characters import CHAMPION_STARTING_HP, Champion, Facing
from gupb.model.coordinates import Coords, sub_coords, add_coords
from gupb.model.weapons import Knife, Sword, Bow, Amulet, Axe

POSSIBLE_ACTIONS = [
    characters.Action.TURN_LEFT,
    characters.Action.TURN_RIGHT,
    characters.Action.STEP_FORWARD,
    characters.Action.ATTACK,
]

MOVE = [
    characters.Action.TURN_LEFT,
    characters.Action.TURN_RIGHT,
    characters.Action.STEP_FORWARD
]

WEAPON_RANKING = {
    'bow_unloaded': 4,
    'bow_loaded': 4,
    'knife': 5,
    'sword': 2,
    'amulet': 1,
    'axe': 3
}

TURN = [characters.Action.TURN_LEFT,
        characters.Action.TURN_RIGHT]

TerrainDescription = Dict[coordinates.Coords, tiles.TileDescription]

LOW_THRESHOLD_HIDDEN_FACTOR = 200
HIGH_THRESHOLD_HIDDEN_FACTOR = 1200

TO_MENHIR_ITERATION = 400
CLOSEST_MIST_ESCAPE = 20

class IntercontinentalBajers(controller.Controller):
    def __init__(self, first_name: str):
        self.first_name: str = first_name
        self.weapon = Knife
        self.champion = None
        self.position = None
        self.discovered_arena: TerrainDescription = dict()
        self.menhir_coords = None
        self.map = None
        self.iteration = 0
        self.arena: Arena = None
        self.hidden_factor_map = dict()
        self.no_of_enemies = None
        self.mist_coords = set()
        self.graph = None
        self.weapons_to_change = []
        self.threat_coords = []
        self.path = None
    def __eq__(self, other: object) -> bool:
        if isinstance(other, IntercontinentalBajers):
            return self.first_name == other.first_name
        return False

    def __hash__(self) -> int:
        return hash(self.first_name)

    def decide(self, knowledge: characters.ChampionKnowledge) -> characters.Action:
        self.iteration += 1
        self._update_discovered_arena(knowledge.visible_tiles)
        self.position = knowledge.position
        self.champion = knowledge.visible_tiles[knowledge.position].character
        self.no_of_enemies = knowledge.no_of_champions_alive

        # if len(self.threat_coords) > 0:
        #     closest_spot = self.get_closest_from()
        #     self.find_path(closest_spot)
        #     return self.next_action(Coords(*self.path[0]))

        if len(self.mist_coords) > 5:
            min_dist, min_coords = self.find_closest_mist()
            if min_dist < CLOSEST_MIST_ESCAPE:
                vector_to_move = sub_coords(self.position, min_coords)
                target = add_coords(self.position, vector_to_move)
                return self.go_to_target_with_random_turn(target)

        if self.is_enemy_in_front_of() and self.champion.health >= 0.3 * CHAMPION_STARTING_HP:
            return characters.Action.ATTACK

        if self.iteration > TO_MENHIR_ITERATION or self.no_of_enemies > 4:
            closest_spot = self.get_closest_from()
            return self.go_to_target_with_random_turn(closest_spot)
        elif self.menhir_coords:
            return self.go_to_target_with_random_turn(self.menhir_coords)

        if self._is_available_step_forward():
            return random.choices(MOVE, weights=[1, 1, 8], k=1)[0]

        return random.choice(TURN)

    def go_to_target_with_random_turn(self, target_coords):
        coords_after_step_forward = self.position + self.champion.facing.value
        if distance(coords_after_step_forward, target_coords) < distance(self.position,
                                                                              target_coords) and self._is_available_step_forward():
            return characters.Action.STEP_FORWARD
        coords_after_step_left = self.position + Facing.LEFT.value
        if distance(coords_after_step_left, target_coords) < distance(self.position, target_coords):
            return characters.Action.TURN_LEFT
        if distance(coords_after_step_left, target_coords) > distance(self.position, target_coords):
            return characters.Action.TURN_RIGHT
        return characters.Action.STEP_FORWARD


    def _update_discovered_arena(self, visible_tiles: Dict[coordinates.Coords, tiles.TileDescription]):
        for coords, description in visible_tiles.items():
            self.discovered_arena[coords] = description
            if not self.menhir_coords and self.check_menhir(coords):
                self.menhir_coords = coords
            if self.is_loot(coords) and self.is_better_weapon(description.loot.name):
                self.weapons_to_change.append(coords)
            if self.discovered_arena[coords].character and \
                self.discovered_arena[coords].character.controller_name != self.first_name:
                self.threat_coords = self.get_threaten_place(coords, self.discovered_arena[coords].character.facing, self.discovered_arena[coords].character.weapon.name)
            if self.check_mist(coords):
                self.mist_coords.add(coords)

    def check_menhir(self, coords: coordinates.Coords):
        return self.discovered_arena[coords].type == 'menhir'
    def is_loot(self, coords):
        return self.discovered_arena[coords].loot

    def is_better_weapon(self, new_name):
        return WEAPON_RANKING[new_name] < WEAPON_RANKING[self.weapon.description()]

    def get_threaten_place(self, pos: Coords, facing: characters.Facing, weapon: str) -> \
    List[Coords]:
        weapon_methods = {
            'knife': partial(weapons.Knife.cut_positions, self.arena.terrain),
            'sword': partial(weapons.Sword.cut_positions, self.arena.terrain),
            'bow_loaded': partial(weapons.Bow.cut_positions, self.arena.terrain),
            'amulet': partial(weapons.Amulet.cut_positions, self.arena.terrain),
            'axe': partial(weapons.Axe.cut_positions, self.arena.terrain)
        }
        pos = Coords(x=pos[0], y=pos[1])
        cut_positions_fn = weapon_methods[weapon]
        return cut_positions_fn(pos, facing)

    def next_action(self, next_position: Coords) -> characters.Action:
        action_map = defaultdict(lambda: characters.Action.TURN_RIGHT, {
            self.position + self.champion.facing.value: characters.Action.STEP_FORWARD,
            self.position + self.champion.facing.turn_left().value: characters.Action.TURN_LEFT
        })
        return action_map[next_position]
    def find_closest_mist(self):
        min_dist = self.arena.size[0] * 2
        min_coords = None
        for coords in self.mist_coords:
            dist = distance(self.position, coords)
            if dist < min_dist:
                min_dist = dist
                min_coords = coords
        return min_dist, min_coords
    def check_mist(self, coords: coordinates.Coords):
        return self.discovered_arena[coords].type == 'mist'

    def _is_available_step_forward(self):
        next_position_coords = self.position + self.champion.facing.value
        return self.is_passable_discovered(next_position_coords)

    def is_passable_discovered(self, next_position_coords: Coords):
        return self.discovered_arena[next_position_coords].type != 'wall' and self.discovered_arena[
            next_position_coords].type != 'sea'

    def is_enemy_in_front_of(self):
        front_coords = self.position + self.champion.facing.value
        return self.discovered_arena[front_coords].character

    def praise(self, score: int) -> None:
        pass

    def reset(self, arena_description: arenas.ArenaDescription) -> None:
        self.weapon = Knife
        self.champion = None
        self.position = None
        self.discovered_arena: TerrainDescription = dict()
        self.arena = Arena.load(arena_description.name)
        self.build_graph()
        self.create_hidden_factor_map()
        self.iteration = 0
        self.mist_coords = set()
        self.graph = None

    def create_hidden_factor_map(self):
        self.hidden_factor_map = dict()
        for coords in self.arena.terrain:
            if self.is_passable_in_arena(self.arena, coords):
                hidden_factor = self.calculate_total_hidden_factor(coords)
                self.hidden_factor_map[coords] = hidden_factor

    def is_passable_in_arena(self, arena: Arena, coords: Coords):
        tile_type = arena.terrain[coords].description().type
        return tile_type != 'wall' and tile_type != 'sea'

    def calculate_total_hidden_factor(self, position: coordinates.Coords):
        sum = 0
        for f in Facing:
            sum += self.calculate_hidden_factor(position, f)
        return sum

    def calculate_hidden_factor(self, position: coordinates.Coords, facing: Facing):
        champion = Champion(position, self.arena)
        champion.facing = facing
        return len(Arena.visible_coords(self.arena, champion))



    def get_closest_from(self):
        min_dist = self.arena.size[0] * 2
        min_coords = self.position
        for coords in self.hidden_factor_map:
            hidden_factor = self.hidden_factor_map[coords]
            if hidden_factor < LOW_THRESHOLD_HIDDEN_FACTOR:
                dist = taxi_distance(self.position, coords)
                if dist < min_dist:
                    min_dist = dist
                    min_coords = coords
        return min_coords

    def build_graph(self):
        self.graph = [[1 if self.is_passable(coords) else 0 for coords in
                   self.arena.terrain] for y in range(self.arena.size[1])]

    def find_path(self, target):
        grid = Grid(matrix=self.graph)
        start = grid.node(self.position[0], self.position[1])
        end = grid.node(target[0], target[1])
        finder = AStarFinder(diagonal_movement=DiagonalMovement.never)
        path, _ = finder.find_path(start,end, grid)
        return path[1:]

    def is_passable(self, coords):
        return self.arena.terrain.get(coords).passable

    @property
    def name(self) -> str:
        return f'{self.first_name}'

    @property
    def preferred_tabard(self) -> characters.Tabard:
        return characters.Tabard.BLUE


def distance(coords, other_coords):
    return sqrt((coords[0] - other_coords[0]) ** 2 + (coords[1] - other_coords[1]) ** 2)

def taxi_distance(coords, other_coords):
    return abs(coords[0] - other_coords[0]) + abs(coords[1] - other_coords[1])

