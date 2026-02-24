import { calculateTrajectory, simulateDelivery } from './src/gameEngine';

const fieldConfig = [
  { x: 0, y: -5, name: 'Wicketkeeper' },
  { x: 5, y: 20, name: 'Mid-off' },
  { x: -5, y: 20, name: 'Mid-on' },
  { x: 15, y: 35, name: 'Cover' },
  { x: -15, y: 35, name: 'Midwicket' },
  { x: 25, y: 50, name: 'Extra Cover' },
  { x: -25, y: 50, name: 'Deep Midwicket' },
  { x: 0, y: 55, name: 'Long-off' },
  { x: 35, y: 30, name: 'Point' },
  { x: -35, y: 30, name: 'Square Leg' },
];

const speed = 80;
const angle = 30;
const elevation = 10;

const trajectory = calculateTrajectory(speed, angle, elevation);
console.log("Total distance:", trajectory.projected_distance.toFixed(1), "m");
console.log("Final position: (", trajectory.final_x.toFixed(1), ",", trajectory.final_y.toFixed(1), ")");

const result = simulateDelivery(
  speed, angle, elevation,
  trajectory.final_x, trajectory.final_y, trajectory.projected_distance, trajectory.max_height,
  fieldConfig, 70.0, 'medium'
);

console.log("\n=== RESULT ===");
console.log("Outcome:", result.outcome);
console.log("Runs:", result.runs);
console.log("Fielder:", result.fielder_involved);
console.log("Fielding position:", result.fielding_position);
