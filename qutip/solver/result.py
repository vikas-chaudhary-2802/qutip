""" Class for solve function results"""
import numpy as np
from ..core import Qobj, QobjEvo, expect, isket, ket2dm, qzero, qzero_like

__all__ = ["Result", "MultiTrajResult", "McResult", "NmmcResult"]


class _QobjExpectEop:
    """
    Pickable e_ops callable that calculates the expectation value for a given
    operator.

    Parameters
    ----------
    op : :obj:`~Qobj`
        The expectation value operator.
    """
    def __init__(self, op):
        self.op = op

    def __call__(self, t, state):
        return expect(self.op, state)


class ExpectOp:
    """
    A result e_op (expectation operation).

    Parameters
    ----------
    op : object
        The original object used to define the e_op operation, e.g. a
        :~obj:`Qobj` or a function ``f(t, state)``.

    f : function
        A callable ``f(t, state)`` that will return the value of the e_op
        for the specified state and time.

    append : function
        A callable ``append(value)``, e.g. ``expect[k].append``, that will
        store the result of the e_ops function ``f(t, state)``.

    Attributes
    ----------
    op : object
        The original object used to define the e_op operation.
    """
    def __init__(self, op, f, append):
        self.op = op
        self._f = f
        self._append = append

    def __call__(self, t, state):
        """
        Return the expectation value for the given time, ``t`` and
        state, ``state``.
        """
        return self._f(t, state)

    def _store(self, t, state):
        """
        Store the result of the e_op function. Should only be called by
        :class:`~Result`.
        """
        self._append(self._f(t, state))


class _BaseResult:
    """
    Common method for all ``Result``.
    """
    def __init__(self, options, *, solver=None, stats=None):
        self.solver = solver
        if stats is None:
            stats = {}
        self.stats = stats

        self._state_processors = []
        self._state_processors_require_copy = False

        self.options = options

    def _e_ops_to_dict(self, e_ops):
        """ Convert the supplied e_ops to a dictionary of Eop instances. """
        if e_ops is None:
            e_ops = {}
        elif isinstance(e_ops, (list, tuple)):
            e_ops = {k: e_op for k, e_op in enumerate(e_ops)}
        elif isinstance(e_ops, dict):
            pass
        else:
            e_ops = {0: e_ops}
        return e_ops

    def add_processor(self, f, requires_copy=False):
        """
        Append a processor ``f`` to the list of state processors.

        Parameters
        ----------
        f : function, ``f(t, state)``
            A function to be called each time a state is added to this
            result object. The state is the state passed to ``.add``, after
            applying the pre-processors, if any.

        requires_copy : bool, default False
            Whether this processor requires a copy of the state rather than
            a reference. A processor must never modify the supplied state, but
            if a processor stores the state it should set ``require_copy`` to
            true.
        """
        self._state_processors.append(f)
        self._state_processors_require_copy |= requires_copy


class Result(_BaseResult):
    """
    Base class for storing solver results.

    Parameters
    ----------
    e_ops : :obj:`~Qobj`, :obj:`~QobjEvo`, function or list or dict of these
        The ``e_ops`` parameter defines the set of values to record at
        each time step ``t``. If an element is a :obj:`~Qobj` or
        :obj:`~QobjEvo` the value recorded is the expectation value of that
        operator given the state at ``t``. If the element is a function, ``f``,
        the value recorded is ``f(t, state)``.

        The values are recorded in the ``e_data`` and ``expect`` attributes of
        this result object. ``e_data`` is a dictionary and ``expect`` is a
        list, where each item contains the values of the corresponding
        ``e_op``.

    options : dict
        The options for this result class.

    solver : str or None
        The name of the solver generating these results.

    stats : dict or None
        The stats generated by the solver while producing these results. Note
        that the solver may update the stats directly while producing results.

    kw : dict
        Additional parameters specific to a result sub-class.

    Attributes
    ----------
    times : list
        A list of the times at which the expectation values and states were
        recorded.

    states : list of :obj:`~Qobj`
        The state at each time ``t`` (if the recording of the state was
        requested).

    final_state : :obj:`~Qobj`:
        The final state (if the recording of the final state was requested).

    expect : list of arrays of expectation values
        A list containing the values of each ``e_op``. The list is in
        the same order in which the ``e_ops`` were supplied and empty if
        no ``e_ops`` were given.

        Each element is itself a list and contains the values of the
        corresponding ``e_op``, with one value for each time in ``.times``.

        The same lists of values may be accessed via the ``.e_data`` dictionary
        and the original ``e_ops`` are available via the ``.e_ops`` attribute.

    e_data : dict
        A dictionary containing the values of each ``e_op``. If the ``e_ops``
        were supplied as a dictionary, the keys are the same as in
        that dictionary. Otherwise the keys are the index of the ``e_op``
        in the ``.expect`` list.

        The lists of expectation values returned are the *same* lists as
        those returned by ``.expect``.

    e_ops : dict
        A dictionary containing the supplied e_ops as ``ExpectOp`` instances.
        The keys of the dictionary are the same as for ``.e_data``.
        Each value is object where ``.e_ops[k](t, state)`` calculates the
        value of ``e_op`` ``k`` at time ``t`` and the given ``state``, and
        ``.e_ops[k].op`` is the original object supplied to create the
        ``e_op``.

    solver : str or None
        The name of the solver generating these results.

    stats : dict or None
        The stats generated by the solver while producing these results.

    options : dict
        The options for this result class.
    """
    def __init__(self, e_ops, options, *, solver=None, stats=None, **kw):
        super().__init__(options, solver=solver, stats=stats)
        raw_ops = self._e_ops_to_dict(e_ops)
        self.e_data = {k: [] for k in raw_ops}
        self.e_ops = {}
        for k, op in raw_ops.items():
            f = self._e_op_func(op)
            self.e_ops[k] = ExpectOp(op, f, self.e_data[k].append)
            self.add_processor(self.e_ops[k]._store)

        self.times = []
        self.states = []
        self.final_state = None

        self._post_init(**kw)

    def _e_op_func(self, e_op):
        """
        Convert an e_op entry into a function, ``f(t, state)`` that returns
        the appropriate value (usually an expectation value).

        Sub-classes may override this function to calculate expectation values
        in different ways.
        """
        if isinstance(e_op, Qobj):
            return _QobjExpectEop(e_op)
        elif isinstance(e_op, QobjEvo):
            return e_op.expect
        elif callable(e_op):
            return e_op
        raise TypeError(f"{e_op!r} has unsupported type {type(e_op)!r}.")

    def _post_init(self):
        """
        Perform post __init__ initialisation. In particular, add state
        processors or pre-processors.

        Sub-class may override this. If the sub-class wishes to register the
        default processors for storing states, it should call this parent
        ``.post_init()`` method.

        Sub-class ``.post_init()`` implementation may take additional keyword
        arguments if required.
        """
        store_states = self.options['store_states']
        store_final_state = self.options['store_final_state']

        if store_states is None:
            store_states = len(self.e_ops) == 0
        if store_states:
            self.add_processor(self._store_state, requires_copy=True)

        if store_states or store_final_state:
            self.add_processor(self._store_final_state, requires_copy=True)

    def _store_state(self, t, state):
        """ Processor that stores a state in ``.states``. """
        self.states.append(state)

    def _store_final_state(self, t, state):
        """ Processor that writes the state to ``.final_state``. """
        self.final_state = state

    def _pre_copy(self, state):
        """ Return a copy of the state. Sub-classes may override this to
            copy a state in different manner or to skip making a copy
            altogether if a copy is not necessary.
        """
        return state.copy()

    def add(self, t, state):
        """
        Add a state to the results for the time ``t`` of the evolution.

        Adding a state calculates the expectation value of the state for
        each of the supplied ``e_ops`` and stores the result in ``.expect``.

        The state is recorded in ``.states`` and ``.final_state`` if specified
        by the supplied result options.

        Parameters
        ----------
        t : float
            The time of the added state.

        state : typically a :obj:`~Qobj`
            The state a time ``t``. Usually this is a :obj:`~Qobj` with
            suitable dimensions, but it sub-classes of result might support
            other forms of the state.

        .. note::

           The expectation values, i.e. ``e_ops``, and states are recorded by
           the state processors (see ``.add_processor``).

           Additional processors may be added by sub-classes.
        """
        self.times.append(t)

        if self._state_processors_require_copy:
            state = self._pre_copy(state)

        for op in self._state_processors:
            op(t, state)

    def __repr__(self):
        lines = [
            f"<{self.__class__.__name__}",
            f"  Solver: {self.solver}",
        ]
        if self.stats:
            lines.append("  Solver stats:")
            lines.extend(
                f"    {k}: {v!r}"
                for k, v in self.stats.items()
            )
        if self.times:
            lines.append(
                f"  Time interval: [{self.times[0]}, {self.times[-1]}]"
                f" ({len(self.times)} steps)"
            )
        lines.append(f"  Number of e_ops: {len(self.e_ops)}")
        if self.states:
            lines.append("  States saved.")
        elif self.final_state is not None:
            lines.append("  Final state saved.")
        else:
            lines.append("  State not saved.")
        lines.append(">")
        return "\n".join(lines)

    @property
    def expect(self):
        return [np.array(e_op) for e_op in self.e_data.values()]


class MultiTrajResult(_BaseResult):
    """
    Base class for storing results for solver using multiple trajectories.

    Parameters
    ----------
    e_ops : :obj:`~Qobj`, :obj:`~QobjEvo`, function or list or dict of these
        The ``e_ops`` parameter defines the set of values to record at
        each time step ``t``. If an element is a :obj:`~Qobj` or
        :obj:`~QobjEvo` the value recorded is the expectation value of that
        operator given the state at ``t``. If the element is a function, ``f``,
        the value recorded is ``f(t, state)``.

        The values are recorded in the ``.expect`` attribute of this result
        object. ``.expect`` is a list, where each item contains the values
        of the corresponding ``e_op``.

        Function ``e_ops`` must return a number so the average can be computed.

    options : dict
        The options for this result class.

    solver : str or None
        The name of the solver generating these results.

    stats : dict or None
        The stats generated by the solver while producing these results. Note
        that the solver may update the stats directly while producing results.

    kw : dict
        Additional parameters specific to a result sub-class.

    Properties
    ----------
    times : list
        A list of the times at which the expectation values and states were
        recorded.

    average_states : list of :obj:`~Qobj`
        The state at each time ``t`` (if the recording of the state was
        requested) averaged over all trajectories as a density matrix.

    runs_states : list of list of :obj:`~Qobj`
        The state for each trajectory and each time ``t`` (if the recording of
        the states and trajectories was requested)

    final_state : :obj:`~Qobj:
        The final state (if the recording of the final state was requested)
        averaged over all trajectories as a density matrix.

    runs_final_state : list of :obj:`~Qobj`
        The final state for each trajectory (if the recording of the final
        state and trajectories was requested).

    average_expect : list of array of expectation values
        A list containing the values of each ``e_op`` averaged over each
        trajectories. The list is in the same order in which the ``e_ops`` were
        supplied and empty if no ``e_ops`` were given.

        Each element is itself an array and contains the values of the
        corresponding ``e_op``, with one value for each time in ``.times``.

    std_expect : list of array of expectation values
        A list containing the standard derivation of each ``e_op`` over each
        trajectories. The list is in the same order in which the ``e_ops`` were
        supplied and empty if no ``e_ops`` were given.

        Each element is itself an array and contains the values of the
        corresponding ``e_op``, with one value for each time in ``.times``.

    runs_expect : list of array of expectation values
        A list containing the values of each ``e_op`` for each trajectories.
        The list is in the same order in which the ``e_ops`` were
        supplied and empty if no ``e_ops`` were given. Only available if the
        storing of trajectories was requested.

        The order of the elements is ``runs_expect[e_ops][trajectory][time]``.

        Each element is itself an array and contains the values of the
        corresponding ``e_op``, with one value for each time in ``.times``.

    average_e_data : dict
        A dictionary containing the values of each ``e_op`` averaged over each
        trajectories. If the ``e_ops`` were supplied as a dictionary, the keys
        are the same as in that dictionary. Otherwise the keys are the index of
        the ``e_op`` in the ``.expect`` list.

        The lists of expectation values returned are the *same* lists as
        those returned by ``.expect``.

    average_e_data : dict
        A dictionary containing the standard derivation of each ``e_op`` over
        each trajectories. If the ``e_ops`` were supplied as a dictionary, the
        keys are the same as in that dictionary. Otherwise the keys are the
        index of the ``e_op`` in the ``.expect`` list.

        The lists of expectation values returned are the *same* lists as
        those returned by ``.expect``.

    runs_e_data : dict
        A dictionary containing the values of each ``e_op`` for each
        trajectories. If the ``e_ops`` were supplied as a dictionary, the keys
        are the same as in that dictionary. Otherwise the keys are the index of
        the ``e_op`` in the ``.expect`` list. Only available if the storing
        of trajectories was requested.

        The order of the elements is ``runs_expect[e_ops][trajectory][time]``.

        The lists of expectation values returned are the *same* lists as
        those returned by ``.expect``.

    solver : str or None
        The name of the solver generating these results.

    stats : dict or None
        The stats generated by the solver while producing these results.

    options : :obj:`~SolverResultsOptions`
        The options for this result class.
    """
    def __init__(self, e_ops, options, *, solver=None, stats=None, **kw):
        super().__init__(options, solver=solver, stats=stats)
        self._raw_ops = self._e_ops_to_dict(e_ops)

        self.times = []
        self.trajectories = []
        self.num_trajectories = 0
        self.seeds = []

        self._sum_states = None
        self._sum_final_states = None
        self._sum_expect = None
        self._sum2_expect = None
        self._target_tols = None

        self.average_e_data = {}
        self.e_data = {}
        self.std_e_data = {}
        self.runs_e_data = {}

        self._post_init(**kw)

    @staticmethod
    def _to_dm(state):
        if state.type == 'ket':
            state = state.proj()
        return state

    def _add_first_traj(self, trajectory):
        """
        Read the first trajectory, intitializing needed data.
        """
        self.times = trajectory.times

        if trajectory.states:
            state = trajectory.states[0]
            self._sum_states = [qzero_like(self._to_dm(state))
                                for state in trajectory.states]
        if trajectory.final_state:
            state = trajectory.final_state
            self._sum_final_states = qzero_like(self._to_dm(state))

        self._sum_expect = [
            np.zeros_like(expect) for expect in trajectory.expect
        ]
        self._sum2_expect = [
            np.zeros_like(expect) for expect in trajectory.expect
        ]

        self.e_ops = trajectory.e_ops

        self.average_e_data = {
            k: list(avg_expect)
            for k, avg_expect
            in zip(self._raw_ops, self._sum_expect)
        }
        self.e_data = self.average_e_data
        if self.options['keep_runs_results']:
            self.runs_e_data = {k: [] for k in self._raw_ops}
            self.e_data = self.runs_e_data

    def _store_trajectory(self, trajectory):
        self.trajectories.append(trajectory)

    def _reduce_states(self, trajectory):
        self._sum_states = [
            accu + self._to_dm(state)
            for accu, state
            in zip(self._sum_states, trajectory.states)
        ]

    def _reduce_final_state(self, trajectory):
        self._sum_final_states += self._to_dm(trajectory.final_state)

    def _reduce_expect(self, trajectory):
        """
        Compute the average of the expectation values and store it in it's
        multiple formats.
        """
        for i, k in enumerate(self._raw_ops):
            expect_traj = trajectory.expect[i]

            self._sum_expect[i] += expect_traj
            self._sum2_expect[i] += expect_traj**2

            avg = self._sum_expect[i] / self.num_trajectories
            avg2 = self._sum2_expect[i] / self.num_trajectories

            self.average_e_data[k] = list(avg)

            # mean(expect**2) - mean(expect)**2 can something be very small
            # negative (-1e-15) which raise an error for float sqrt.
            self.std_e_data[k] = list(np.sqrt(np.abs(avg2 - np.abs(avg**2))))

            if self.runs_e_data:
                self.runs_e_data[k].append(trajectory.e_data[k])

    def _increment_traj(self, trajectory):
        if self.num_trajectories == 0:
            self._add_first_traj(trajectory)
        self.num_trajectories += 1

    def _no_end(self):
        """
        Remaining number of trajectories needed to finish cannot be determined
        by this object.
        """
        return np.inf

    def _fixed_end(self):
        """
        Finish at a known number of trajectories.
        """
        ntraj_left = self._target_ntraj - self.num_trajectories
        if ntraj_left == 0:
            self.stats['end_condition'] = 'ntraj reached'
        return ntraj_left

    def _target_tolerance_end(self):
        """
        Compute the error on the expectation values using jackknife resampling.
        Return the approximate number of trajectories needed to have this
        error within the tolerance fot all e_ops and times.
        """
        if self.num_trajectories <= 1:
            return np.inf
        avg = np.array(self._sum_expect) / self.num_trajectories
        avg2 = np.array(self._sum2_expect) / self.num_trajectories
        target = np.array([
            atol + rtol * mean
            for mean, (atol, rtol)
            in zip(avg, self._target_tols)
        ])
        target_ntraj = np.max((avg2 - abs(avg)**2) / target**2 + 1)

        self._estimated_ntraj = min(target_ntraj, self._target_ntraj)
        if (self._estimated_ntraj - self.num_trajectories) <= 0:
            self.stats['end_condition'] = 'target tolerance reached'
        return self._estimated_ntraj - self.num_trajectories

    def _post_init(self):
        self.num_trajectories = 0
        self._target_ntraj = None

        store_states = self.options['store_states']
        store_final_state = self.options['store_final_state']
        store_traj = self.options['keep_runs_results']

        self.add_processor(self._increment_traj)
        if store_traj:
            self.add_processor(self._store_trajectory)
        if store_states is None:
            store_states = len(self._raw_ops) == 0
        if store_states:
            self.add_processor(self._reduce_states)
        if store_states or store_final_state:
            self.add_processor(self._reduce_final_state)
        if self._raw_ops:
            self.add_processor(self._reduce_expect)

        self._early_finish_check = self._no_end
        self.stats['end_condition'] = 'unknown'

    def add(self, trajectory_info):
        """
        Add a trajectory to the evolution.

        Trajectories can be saved or average canbe extracted depending on the
        options ``keep_runs_results``.

        Parameters
        ----------
        trajectory_info : tuple of seed and trajectory
            - seed: int, SeedSequence
              Seed used to generate the trajectory.
            - trajectory : :class:`Result`
              Run result for one evolution over the times.

        Return
        ------
        remaing_traj : number
            Return the number of trajectories still needed to reach the target
            tolerance. If no tolerance is provided, return infinity.
        """
        seed, trajectory = trajectory_info
        self.seeds.append(seed)

        for op in self._state_processors:
            op(trajectory)

        return self._early_finish_check()

    def add_end_condition(self, ntraj, target_tol=None):
        """
        Set the condition to stop the computing trajectories when the certain
        condition are fullfilled.
        Supported end condition for multi trajectories computation are:
        - Reaching a number of trajectories.
        - Error bar on the expectation values reach smaller than a given
          tolerance.

        Parameters
        ----------
        ntraj : int
            Number of trajectories expected.

        target_tol : float, array_like, [optional]
            Target tolerance of the evolution. The evolution will compute
            trajectories until the error on the expectation values is lower
            than this tolerance. The error is computed using jackknife
            resampling. ``target_tol`` can be an absolute tolerance, a pair of
            absolute and relative tolerance, in that order. Lastly, it can be a
            list of pairs of (atol, rtol) for each e_ops.

            Error estimation is done with jackknife resampling.
        """
        self._target_ntraj = ntraj
        self.stats['end_condition'] = 'timeout'

        if target_tol is None:
            self._early_finish_check = self._fixed_end
            return

        num_e_ops = len(self._raw_ops)

        if not num_e_ops:
            raise ValueError("Cannot target a tolerance without e_ops")

        self._estimated_ntraj = ntraj

        targets = np.array(target_tol)
        if targets.ndim == 0:
            self._target_tols = np.array([(target_tol, 0.)] * num_e_ops)
        elif targets.shape == (2,):
            self._target_tols = np.ones((num_e_ops, 2)) * targets
        elif targets.shape == (num_e_ops, 2):
            self._target_tols = targets
        else:
            raise ValueError("target_tol must be a number, a pair of (atol, "
                             "rtol) or a list of (atol, rtol) for each e_ops")

        self._early_finish_check = self._target_tolerance_end

    @property
    def runs_states(self):
        """
        States of every runs as ``states[run][t]``.
        """
        if self.trajectories and self.trajectories[0].states:
            return [traj.states for traj in self.trajectories]
        else:
            return None

    @property
    def average_states(self):
        """
        States averages as density matrices.
        """
        if self._sum_states is None:
            return None
        return [final / self.num_trajectories for final in self._sum_states]

    @property
    def states(self):
        """
        Runs final states if available, average otherwise.
        """
        return self.runs_states or self.average_states

    @property
    def runs_final_states(self):
        """
        Last states of each trajectories.
        """
        if self.trajectories and self.trajectories[0].final_state:
            return [traj.final_state for traj in self.trajectories]
        else:
            return None

    @property
    def average_final_state(self):
        """
        Last states of each trajectories averaged into a density matrix.
        """
        if self._sum_final_states is None:
            return None
        return self._sum_final_states / self.num_trajectories

    @property
    def final_state(self):
        """
        Runs final states if available, average otherwise.
        """
        return self.runs_final_states or self.average_final_state

    @property
    def average_expect(self):
        return [np.array(val) for val in self.average_e_data.values()]

    @property
    def std_expect(self):
        return [np.array(val) for val in self.std_e_data.values()]

    @property
    def runs_expect(self):
        return [np.array(val) for val in self.runs_e_data.values()]

    @property
    def expect(self):
        return [np.array(val) for val in self.e_data.values()]

    def steady_state(self, N=0):
        """
        Average the states of the last ``N`` times of every runs as a density
        matrix. Should converge to the steady state in the right circumstances.

        Parameters
        ----------
        N : int [optional]
            Number of states from the end of ``tlist`` to average. Per default
            all states will be averaged.
        """
        N = int(N) or len(self.times)
        N = len(self.times) if N > len(self.times) else N
        states = self.average_states
        if states is not None:
            return sum(states[-N:]) / N
        else:
            return None

    def __repr__(self):
        lines = [
            f"<{self.__class__.__name__}",
            f"  Solver: {self.solver}",
        ]
        if self.stats:
            lines.append("  Solver stats:")
            lines.extend(
                f"    {k}: {v!r}"
                for k, v in self.stats.items()
            )
        if self.times:
            lines.append(
                f"  Time interval: [{self.times[0]}, {self.times[-1]}]"
                f" ({len(self.times)} steps)"
            )
        lines.append(f"  Number of e_ops: {len(self.e_ops)}")
        if self.states:
            lines.append("  States saved.")
        elif self.final_state is not None:
            lines.append("  Final state saved.")
        else:
            lines.append("  State not saved.")
        lines.append(f"  Number of trajectories: {self.num_trajectories}")
        if self.trajectories:
            lines.append("  Trajectories saved.")
        else:
            lines.append("  Trajectories not saved.")
        lines.append(">")
        return "\n".join(lines)

    def __add__(self, other):
        if not isinstance(other, MultiTrajResult):
            return NotImplemented
        if self._raw_ops != other._raw_ops:
            raise ValueError("Shared `e_ops` is required to merge results")
        if self.times != other.times:
            raise ValueError("Shared `times` are is required to merge results")
        new = self.__class__(self._raw_ops, self.options,
                             solver=self.solver, stats=self.stats)
        if self.trajectories and other.trajectories:
            new.trajectories = self.trajectories + other.trajectories
        new.num_trajectories = self.num_trajectories + other.num_trajectories
        new.times = self.times
        new.seeds = self.seeds + other.seeds

        if self._sum_states is not None and other._sum_states is not None:
            new._sum_states = self._sum_states + other._sum_states

        if (
            self._sum_final_states is not None
            and other._sum_final_states is not None
        ):
            new._sum_final_states = (
                self._sum_final_states + other._sum_final_states
            )
        new._target_tols = None

        new._sum_expect = []
        new._sum2_expect = []
        new.average_e_data = {}
        new.std_e_data = {}

        for i, k in enumerate(self._raw_ops):
            new._sum_expect.append(self._sum_expect[i] + other._sum_expect[i])
            new._sum2_expect.append(self._sum2_expect[i]
                                    + other._sum2_expect[i])

            avg = new._sum_expect[i] / new.num_trajectories
            avg2 = new._sum2_expect[i] / new.num_trajectories

            new.average_e_data[k] = list(avg)
            new.e_data = new.average_e_data

            new.std_e_data[k] = np.sqrt(np.abs(avg2 - np.abs(avg**2)))

            if new.trajectories:
                new.runs_e_data[k] = self.runs_e_data[k] + other.runs_e_data[k]
                new.e_data = new.runs_e_data

        new.stats["run time"] += other.stats["run time"]
        new.stats['end_condition'] = "Merged results"

        return new


class McTrajectoryResult(Result):
    """
    Result class used by the :class:`qutip.MCSolver` for single trajectories.
    """

    def __init__(self, e_ops, options, *args, **kwargs):
        super().__init__(e_ops, {**options, "normalize_output": False},
                         *args, **kwargs)


class McResult(MultiTrajResult):
    """
    Class for storing Monte-Carlo solver results.

    Parameters
    ----------
    e_ops : :obj:`~Qobj`, :obj:`~QobjEvo`, function or list or dict of these
        The ``e_ops`` parameter defines the set of values to record at
        each time step ``t``. If an element is a :obj:`~Qobj` or
        :obj:`~QobjEvo` the value recorded is the expectation value of that
        operator given the state at ``t``. If the element is a function, ``f``,
        the value recorded is ``f(t, state)``.

        The values are recorded in the ``.expect`` attribute of this result
        object. ``.expect`` is a list, where each item contains the values
        of the corresponding ``e_op``.

    options : :obj:`~SolverResultsOptions`
        The options for this result class.

    solver : str or None
        The name of the solver generating these results.

    stats : dict
        The stats generated by the solver while producing these results. Note
        that the solver may update the stats directly while producing results.
        Must include a value for "num_collapse".

    kw : dict
        Additional parameters specific to a result sub-class.

    Properties
    ----------
    collapse : list
        For each runs, a list of every collapse as a tuple of the time it
        happened and the corresponding ``c_ops`` index.
    """
    # Collapse are only produced by mcsolve.

    def _add_collapse(self, trajectory):
        self.collapse.append(trajectory.collapse)

    def _post_init(self):
        super()._post_init()
        self.num_c_ops = self.stats["num_collapse"]
        self.collapse = []
        self.add_processor(self._add_collapse)

    @property
    def col_times(self):
        """
        List of the times of the collapses for each runs.
        """
        out = []
        for col_ in self.collapse:
            col = list(zip(*col_))
            col = ([] if len(col) == 0 else col[0])
            out.append(col)
        return out

    @property
    def col_which(self):
        """
        List of the indexes of the collapses for each runs.
        """
        out = []
        for col_ in self.collapse:
            col = list(zip(*col_))
            col = ([] if len(col) == 0 else col[1])
            out.append(col)
        return out

    @property
    def photocurrent(self):
        """
        Average photocurrent or measurement of the evolution.
        """
        cols = [[] for _ in range(self.num_c_ops)]
        tlist = self.times
        for collapses in self.collapse:
            for t, which in collapses:
                cols[which].append(t)
        mesurement = [
            np.histogram(cols[i], tlist)[0] / np.diff(tlist) / self.num_trajectories
            for i in range(self.num_c_ops)
        ]
        return mesurement

    @property
    def runs_photocurrent(self):
        """
        Photocurrent or measurement of each runs.
        """
        tlist = self.times
        measurements = []
        for collapses in self.collapse:
            cols = [[] for _ in range(self.num_c_ops)]
            for t, which in collapses:
                cols[which].append(t)
            measurements.append([
                np.histogram(cols[i], tlist)[0] / np.diff(tlist)
                for i in range(self.num_c_ops)
            ])
        return measurements


class NmmcTrajectoryResult(McTrajectoryResult):
    """
    Result class used by the :class:`qutip.NonMarkovianMCSolver` for single
    trajectories. Additionally stores the trace of the state along the
    trajectory.
    """

    def __init__(self, e_ops, options, *args, **kwargs):
        self._nm_solver = kwargs.pop("__nm_solver")
        super().__init__(e_ops, options, *args, **kwargs)
        self.trace = []

    # This gets called during the Monte-Carlo simulation of the associated
    # completely positive master equation. To obtain the state of the actual
    # system, we simply multiply the provided state with the current martingale
    # before storing it / computing expectation values.
    def add(self, t, state):
        if isket(state):
            state = ket2dm(state)
        mu = self._nm_solver.current_martingale()
        super().add(t, state * mu)
        self.trace.append(mu)

    add.__doc__ = Result.add.__doc__


class NmmcResult(McResult):
    """
    Class for storing the results of the non-Markovian Monte-Carlo solver.

    Parameters
    ----------
    e_ops : :obj:`~Qobj`, :obj:`~QobjEvo`, function or list or dict of these
        The ``e_ops`` parameter defines the set of values to record at
        each time step ``t``. If an element is a :obj:`~Qobj` or
        :obj:`~QobjEvo` the value recorded is the expectation value of that
        operator given the state at ``t``. If the element is a function, ``f``,
        the value recorded is ``f(t, state)``.

        The values are recorded in the ``.expect`` attribute of this result
        object. ``.expect`` is a list, where each item contains the values
        of the corresponding ``e_op``.

    options : :obj:`~SolverResultsOptions`
        The options for this result class.

    solver : str or None
        The name of the solver generating these results.

    stats : dict
        The stats generated by the solver while producing these results. Note
        that the solver may update the stats directly while producing results.
        Must include a value for "num_collapse".

    kw : dict
        Additional parameters specific to a result sub-class.

    Properties
    ----------
    average_trace : list
        The average trace (i.e., averaged over all trajectories) at each time.

    std_trace : list
        The standard deviation of the trace at each time.

    runs_trace : list of lists
        For each recorded trajectory, the trace at each time.
        Only present if ``keep_runs_results`` is set in the options.
    """

    def _post_init(self):
        super()._post_init()

        self._sum_trace = None
        self._sum2_trace = None
        self.average_trace = []
        self.std_trace = []
        self.runs_trace = []

        self.add_processor(self._add_trace)

    def _add_first_traj(self, trajectory):
        super()._add_first_traj(trajectory)
        self._sum_trace = np.zeros_like(trajectory.times)
        self._sum2_trace = np.zeros_like(trajectory.times)

    def _add_trace(self, trajectory):
        new_trace = np.array(trajectory.trace)
        self._sum_trace += new_trace
        self._sum2_trace += np.abs(new_trace)**2

        avg = self._sum_trace / self.num_trajectories
        avg2 = self._sum2_trace / self.num_trajectories

        self.average_trace = avg
        self.std_trace = np.sqrt(np.abs(avg2 - np.abs(avg)**2))

        if self.options['keep_runs_results']:
            self.runs_trace.append(trajectory.trace)

    @property
    def trace(self):
        """
        Refers to ``average_trace`` or ``runs_trace``, depending on whether
        ``keep_runs_results`` is set in the options.
        """
        return self.runs_trace or self.average_trace
