from datetime import datetime, timedelta
import json
import math

import matplotlib.pyplot as plt
import numpy as np

from parts.amortizedmarkov import ProbState
from parts.hospitalized_agegroup import AgeGroup
from parts.constants import *

from scenarios.scenario import EpiScenario

class ScenarioDrivenModel:
	def __init__(self, scenario):
		if isinstance(scenario, str):
			self.scenario = EpiScenario(scenario)
		elif isinstance(scenario, EpiScenario):
			self.scenario = scenario

		self.modelname = self.scenario.modelname
		self.total_days = 0
		self.r0 = self.scenario.parameters['initial_r0']
		self.beta = None
		self.population = self.scenario.totalpop

		self.susceptible = ProbState(period=0, count=self.scenario.init_susceptible, name='susceptible')
		self.incubating = ProbState(period=self.scenario.incubation_period, count=self.scenario.init_infected, name='incubating')
		self.infectious = ProbState(period=self.scenario.prediagnosis, count=self.scenario.init_infectious, name='infectious')
		self.isolated_holding = ProbState(period=90, name='isolated_holding')

		self.incubating.add_exit_state(self.infectious, 1)
		self.incubating.normalize_states_over_period()

		self.infectious.add_exit_state(self.isolated_holding, 1)
		self.infectious.normalize_states_over_period()

		self.subgroups = dict()
		for key, value in self.scenario.subgrouprates.items():
			self.subgroups[key] = AgeGroup(value, name=key)

		self.fitness = None

	def run(self):
		self.run_r0_set(self.scenario.r0_date_offsets, self.scenario.r0_values)

	def set_r0(self, value):
		self.r0 = value

	def recalculate(self):
		self.beta = self.r0 / self.infectious.period

	def run_r0_set(self, date_offsets, r0_values):
		self.scenario.hospital_door_aggregator = []
		day_counter = 0
		for itr in range(0, len(date_offsets)):
			self.set_r0(r0_values[itr])
			self.recalculate()
			while day_counter < date_offsets[itr]:
				self.step_day()
				day_counter += 1

	def step_day(self):
		new_infections = self.beta * self.susceptible.count * self.infectious.count / self.population
		self.susceptible.store_pending(-new_infections)
		self.incubating.store_pending(new_infections)
		self.incubating.pass_downstream()
		self.infectious.pass_downstream()

		diagnosed = self.isolated_holding.pending
		if len(self.scenario.hospital_door_aggregator) == 0:
			diagnagg = diagnosed
		else:
			diagnagg = self.scenario.hospital_door_aggregator[-1] + diagnosed
		self.scenario.hospital_door_aggregator.append(diagnagg)

		self.isolated_holding.pending = 0
		subpop_out = []
		for key, agegroup in self.subgroups.items():
			subpop = diagnosed * agegroup.stats.pop_dist
			subpop_out.append(subpop)
			agegroup.apply_infections(subpop)
			agegroup.calculate_redistributions()
		self.susceptible.apply_pending()
		self.incubating.apply_pending()
		self.infectious.apply_pending()

		for key, agegroup in self.subgroups.items():
			agegroup.apply_pending()

		self.total_days += 1


	def gather_sums(self):
		time_increments = len(self.susceptible.domain)
		self.scenario.out_susceptible = self.susceptible.domain
		self.scenario.out_incubating = self.incubating.domain
		self.scenario.out_infectious = self.infectious.domain
		self.scenario.sum_isolated  = [0] * time_increments
		self.scenario.sum_noncrit   = [0] * time_increments
		self.scenario.sum_icu       = [0] * time_increments
		self.scenario.sum_icu_vent  = [0] * time_increments
		self.scenario.sum_recovered = [0] * time_increments
		self.scenario.sum_deceased  = [0] * time_increments
		self.scenario.sum_hospitalized  = [0] * time_increments

		for key, value in self.subgroups.items():
			self.scenario.sum_isolated  = np.add(self.scenario.sum_isolated, value.isolated.domain)
			self.scenario.sum_noncrit   = np.add(self.scenario.sum_noncrit, value.h_noncrit.domain)
			self.scenario.sum_icu       = np.add(self.scenario.sum_icu, value.h_icu.domain)
			self.scenario.sum_icu_vent  = np.add(self.scenario.sum_icu_vent, value.h_icu_vent.domain)
			self.scenario.sum_recovered = np.add(self.scenario.sum_recovered, value.recovered.domain)
			self.scenario.sum_deceased  = np.add(self.scenario.sum_deceased, value.deceased.domain)

			self.scenario.sum_hospitalized  = np.add(self.scenario.sum_hospitalized, value.h_noncrit.domain)
			self.scenario.sum_hospitalized  = np.add(self.scenario.sum_hospitalized, value.h_icu.domain)
			self.scenario.sum_hospitalized  = np.add(self.scenario.sum_hospitalized, value.h_icu_vent.domain)


	def save_results(self, iteration):
		result = dict()

		result['iteration'] = iteration
		result['fitness'] = self.fitness
		result['scenario'] = self.scenario.parameters

		result['modelname'] = self.modelname
		result['total_days'] = self.total_days
		result['totalpop'] = self.population
		result['sum_isolated'] = self.scenario.sum_isolated
		result['sum_noncrit'] = self.scenario.sum_noncrit
		result['sum_icu'] = self.scenario.sum_icu
		result['sum_icu_vent'] = self.scenario.sum_icu_vent
		result['sum_recovered'] = self.scenario.sum_recovered
		result['sum_deceased'] = self.scenario.sum_deceased

		with open(f"best_fit{iteration}", "w") as bfi:
			json.dump(result, bfi)

	def actual_curves(self):
		cursor = self.scenario.initial_date
		finaldate = cursor + timedelta(self.scenario.maxdays)
		act_hosp = []
		act_death = []

		while cursor < finaldate:
			if cursor in COLORADO_ACTUAL:
				act_hosp.append(COLORADO_ACTUAL[cursor]['hospitalized'])
				act_death.append(COLORADO_ACTUAL[cursor]['deaths'])
			else:
				act_hosp.append(None)
				act_death.append(None)
			cursor += ONEDAY
		act_death.append(None)
		act_hosp.append(None)
		return act_hosp, act_death

# 2709 S. Cook.  Denver, Co. 80210

	def generate_png(self):
		u_susc = self.scenario.out_susceptible
		u_incu = self.scenario.out_incubating
		u_infe = self.scenario.out_infectious
		u_isol = self.scenario.sum_isolated
		u_h_no = self.scenario.sum_noncrit
		u_h_ic = self.scenario.sum_icu
		u_h_ve = self.scenario.sum_icu_vent
		u_reco = self.scenario.sum_recovered
		u_dead = self.scenario.sum_deceased

		startdate = self.scenario.initial_date
		time_domain = [startdate]
		cursor = startdate
		for _ in range(0, self.scenario.maxdays):
			cursor += ONEDAY
			time_domain.append(cursor)

	#	time_domain = np.linspace(0, model.total_days, model.total_days + 1)
		hospitalized = []
		for itr in range(0, len(u_h_no)):
			hospitalized.append(u_h_no[itr] + u_h_ic[itr] + u_h_ve[itr])


		fig = plt.figure(facecolor='w')
		# ax = fig.add_subplot(111, axis_bgcolor='#dddddd', axisbelow=True)
		ax = fig.add_subplot(111, axisbelow=True)

		act_hosp, act_death = self.actual_curves()
		ax.plot(time_domain, act_hosp, color=(0, 0, .5), alpha=1, lw=2, label='Actual Hospitalized', linestyle='-')
		ax.plot(time_domain, act_death, color=(0, 0, .5), alpha=1, lw=2, label='Actual Deaths', linestyle='-')

#   	ax.plot(time_domain, u_susc, color=(0, 0, 1), alpha=.5, lw=2, label='Susceptible', linestyle='-')
#   	ax.plot(time_domain, u_incu, color=TABLEAU_ORANGE, alpha=0.1, lw=2, label='Exposed', linestyle='-')
#   	ax.plot(time_domain, u_infe, color=TABLEAU_RED, alpha=0.5, lw=2, label='Infected', linestyle='-')
#   	ax.plot(time_domain, u_isol, color=TAB_COLORS[8], alpha=.5, lw=2, label='Home Iso', linestyle='-')
#		ax.plot(time_domain, u_h_no, color=TABLEAU_BLUE, alpha=1, lw=2, label='Noncrit', linestyle='--')
#		ax.plot(time_domain, u_h_ic, color=TABLEAU_GREEN, alpha=1, lw=2, label='ICU', linestyle='--')
#		ax.plot(time_domain, u_h_ve, color=TABLEAU_RED, alpha=1, lw=2, label='ICU + Ventilator', linestyle='--')
		ax.plot(time_domain, hospitalized, color=(1, 0, 0), alpha=.25, lw=2, label='Total Hospitalized', linestyle='-')
#   	ax.plot(time_domain, u_reco, color=(0, .5, 0), alpha=.5, lw=2, label='Recovered', linestyle='--')
		ax.plot(time_domain, u_dead, color=(0, 0, 0), alpha=.5, lw=2, label='Dead', linestyle=':')

#		ax.plot(time_domain, [229] * (self.total_days + 1), color=(0, 0, 1), alpha=1, lw=1, label='511 Beds', linestyle='-')
#		ax.plot(time_domain, [86] * (self.total_days + 1), color=(1, 0, 0), alpha=1, lw=1, label='77 ICU units', linestyle='-')

		plt.axvline(x=datetime.today(), alpha=.5, lw=2, label='Today')

		ax.set_xlabel('Days')
		ax.set_ylabel('Number')

		chart_title = self.modelname
		plt.title(chart_title, fontsize=14)
		# ax.set_ylim(0,1.2)
		ax.yaxis.set_tick_params(length=4)
		ax.xaxis.set_tick_params(length=4)
		# ax.grid(b=True, which='minor', c='w', lw=1, ls='--')
		ax.grid()
		legend = ax.legend()
		legend.get_frame().set_alpha(0.5)
		for spine in ('top', 'right', 'bottom', 'left'):
			ax.spines[spine].set_visible(False)

		outfilename = "_".join(chart_title.replace("|", " ").replace(":", " ").replace(".", " ").split())

		# Write a CSV to this directory
		with open(f"{outfilename}.csv", 'w') as outfile:
			for itr in range(0, len(u_susc)):
				outfile.write(f"{u_susc[itr]:.6f}, {u_incu[itr]:.6f}, {u_infe[itr]:.6f}, {u_isol[itr]:.6f}"
						f", {u_h_no[itr]:.6f}, {u_h_ic[itr]:.6f}, {u_h_ve[itr]:.6f}, {u_reco[itr]:.6f}"
						f", {u_dead[itr]:.6f}, {hospitalized[itr]:.6f}\n")

		return plt



ONEDAY = timedelta(1)

def main():
	model = ScenarioDrivenModel('ga_fit.json')

	model.run()
	model.gather_sums()

	thisplot = model.generate_png()
	chart_title = model.modelname
	outfilename = "_".join(chart_title.replace("|", " ").replace(":", " ").replace(".", " ").split())
	thisplot.savefig(f"{outfilename}.png", bbox_inches="tight")

	thisplot.show()


if __name__ == '__main__':
	main()
