"""
Testing 3D horizontal viscosity of momemtum against analytical solution.

Tuomas Karna 2015-12-11
"""
from thetis import *
import numpy
from scipy import stats
import pytest


def run(refinement, **model_options):
    print_output('--- running refinement {:}'.format(refinement))
    warped_mesh = model_options.pop('warped_mesh', False)

    # domain dimensions - channel in x-direction
    lx = 15.0e3
    ly = 6.0e3/refinement
    area = lx*ly
    depth = 40.0
    horizontal_viscosity = 1.0e3

    # mesh
    n_layers = 4*refinement
    nx = 4*refinement + 1
    ny = 1  # constant -- channel
    mesh2d = RectangleMesh(nx, ny, lx, ly)

    # simulation run time
    t_end = 3000.0
    # initial time
    t_init = 1000.0  # NOTE start from t > 0 for smoother init cond
    t_export = (t_end - t_init)/8.0

    # outputs
    outputdir = 'outputs'

    # bathymetry
    p1_2d = FunctionSpace(mesh2d, 'CG', 1)
    bathymetry_2d = Function(p1_2d, name='Bathymetry')
    bathymetry_2d.assign(depth)
    if warped_mesh:
        # linear bathymetry and elevation
        # NOTE should be linear so all meshes can fully resolve geometry
        bathymetry_2d.interpolate(Expression('h + 20.0*x[0]/lx', h=depth, lx=lx))

    solverobj = solver.FlowSolver(mesh2d, bathymetry_2d, n_layers)
    options = solverobj.options
    options.use_nonlinear_equations = False
    options.use_ale_moving_mesh = False
    options.horizontal_velocity_scale = Constant(1.0)
    options.no_exports = True
    options.output_directory = outputdir
    options.simulation_end_time = t_end
    options.simulation_export_time = t_export
    options.solve_salinity = False
    options.solve_temperature = False
    options.fields_to_export = ['uv_3d']
    options.horizontal_viscosity = Constant(horizontal_viscosity)
    options.horizontal_viscosity_scale = Constant(horizontal_viscosity)
    options.update(model_options)
    if hasattr(options.timestepper_options, 'use_automatic_timestep'):
        options.timestepper_options.use_automatic_timestep = True

    solverobj.create_equations()

    t = t_init  # simulation time

    ana_sol_expr = '0.5*(u_max + u_min) - 0.5*(u_max - u_min)*erf((x[0] - x0)/sqrt(4*D*t))'
    t_const = Constant(t)
    ana_uv_expr = Expression((ana_sol_expr, 0.0, 0.0), u_max=1.0, u_min=-1.0, x0=lx/2.0, D=horizontal_viscosity, t=t_const)
    ana_uv_expr_2d = Expression((ana_sol_expr, 0.0), u_max=1.0, u_min=-1.0, x0=lx/2.0, D=horizontal_viscosity, t=t_const)

    uv_ana = Function(solverobj.function_spaces.U, name='uv analytical')
    uv_ana_p1 = Function(solverobj.function_spaces.P1v, name='uv analytical')

    p1dg_v_ho = VectorFunctionSpace(solverobj.mesh, 'DG', options.polynomial_degree + 2,
                                    vfamily='DG', vdegree=options.polynomial_degree + 2)
    uv_ana_ho = Function(p1dg_v_ho, name='uv analytical')
    uv_ana.project(ana_uv_expr)

    elev_init = Function(solverobj.function_spaces.H_2d, name='elev init')
    solverobj.assign_initial_conditions(elev=elev_init, uv_2d=ana_uv_expr_2d)

    # export analytical solution
    if not options.no_exports:
        out_uv_ana = File(os.path.join(options.output_directory, 'uv_ana.pvd'))

    def export_func():
        if not options.no_exports:
            solverobj.export()
            # update analytical solution to correct time
            t_const.assign(t)
            ana_uv_expr = Expression((ana_sol_expr, 0.0, 0.0), u_max=1.0, u_min=-1.0, x0=lx/2.0, D=horizontal_viscosity, t=t_const)
            uv_ana.project(ana_uv_expr)
            out_uv_ana.write(uv_ana_p1.project(uv_ana))

    # export initial conditions
    export_func()

    # custom time loop that solves momemtum eq only
    ti = solverobj.timestepper.timesteppers.mom_expl
    i = 0
    iexport = 1
    next_export_t = t + solverobj.options.simulation_export_time
    while t < t_end - 1e-8:
        ti.advance(t)
        t += solverobj.dt
        i += 1
        if t >= next_export_t - 1e-8:
            print_output('{:3d} i={:5d} t={:8.2f} s uv={:8.2f}'.format(iexport, i, t, norm(solverobj.fields.uv_3d)))
            export_func()
            next_export_t += solverobj.options.simulation_export_time
            iexport += 1

    # project analytical solultion on high order mesh
    t_const.assign(t)
    ana_uv_expr = Expression((ana_sol_expr, 0.0, 0.0), u_max=1.0, u_min=-1.0, x0=lx/2.0, D=horizontal_viscosity, t=t_const)
    uv_ana_ho.project(ana_uv_expr)
    # compute L2 norm
    l2_err = errornorm(uv_ana_ho, solverobj.fields.uv_3d)/numpy.sqrt(area)
    print_output('L2 error {:.12f}'.format(l2_err))

    return l2_err


def run_convergence(ref_list, saveplot=False, **options):
    """Runs test for a list of refinements and computes error convergence rate"""
    polynomial_degree = options.get('polynomial_degree', 1)
    space_str = options.get('element_family')
    l2_err = []
    for r in ref_list:
        l2_err.append(run(r, **options))
    x_log = numpy.log10(numpy.array(ref_list, dtype=float)**-1)
    y_log = numpy.log10(numpy.array(l2_err))
    setup_name = 'h-viscosity'

    def check_convergence(x_log, y_log, expected_slope, field_str, saveplot):
        slope_rtol = 0.20
        slope, intercept, r_value, p_value, std_err = stats.linregress(x_log, y_log)
        if saveplot:
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots(figsize=(5, 5))
            # plot points
            ax.plot(x_log, y_log, 'k.')
            x_min = x_log.min()
            x_max = x_log.max()
            offset = 0.05*(x_max - x_min)
            npoints = 50
            xx = numpy.linspace(x_min - offset, x_max + offset, npoints)
            yy = intercept + slope*xx
            # plot line
            ax.plot(xx, yy, linestyle='--', linewidth=0.5, color='k')
            ax.text(xx[2*npoints/3], yy[2*npoints/3], '{:4.2f}'.format(slope),
                    verticalalignment='top',
                    horizontalalignment='left')
            ax.set_xlabel('log10(dx)')
            ax.set_ylabel('log10(L2 error)')
            ax.set_title(' '.join([setup_name, field_str, 'degree={:}'.format(polynomial_degree), space_str]))
            ref_str = 'ref-' + '-'.join([str(r) for r in ref_list])
            degree_str = 'o{:}'.format(polynomial_degree)
            imgfile = '_'.join(['convergence', setup_name, field_str, ref_str, degree_str, space_str])
            imgfile += '.png'
            imgdir = create_directory('plots')
            imgfile = os.path.join(imgdir, imgfile)
            print_output('saving figure {:}'.format(imgfile))
            plt.savefig(imgfile, dpi=200, bbox_inches='tight')
        if expected_slope is not None:
            err_msg = '{:}: Wrong convergence rate {:.4f}, expected {:.4f}'.format(setup_name, slope, expected_slope)
            assert slope > expected_slope*(1 - slope_rtol), err_msg
            print_output('{:}: convergence rate {:.4f} PASSED'.format(setup_name, slope))
        else:
            print_output('{:}: {:} convergence rate {:.4f}'.format(setup_name, field_str, slope))
        return slope

    check_convergence(x_log, y_log, polynomial_degree+1, 'uv', saveplot)

# ---------------------------
# standard tests for pytest
# ---------------------------

# NOTE mimetic elements do not converge optimally, rate is 1.48


@pytest.fixture(params=[True, False], ids=['warped',
                                           'regular'])
def warped(request):
    return request.param


@pytest.mark.parametrize(('stepper', 'use_ale'),
                         [('SSPRK33', False),
                          ('LeapFrog', True),
                          ('SSPRK22', True)])
@pytest.mark.parametrize(('family', 'polynomial_degree'),
                         [('dg-dg', 0),
                          ('dg-dg', 1),
                          pytest.mark.skip(reason='rt-0 still broken')(('rt-dg', 0)),
                          ('rt-dg', 1)])
def test_horizontal_viscosity(warped, polynomial_degree, family, stepper, use_ale):
    run_convergence([1, 2, 3], polynomial_degree=polynomial_degree, warped_mesh=warped,
                    element_family=family, timestepper_type=stepper,
                    use_ale_moving_mesh=use_ale)

# ---------------------------
# run individual setup for debugging
# ---------------------------


if __name__ == '__main__':
    run_convergence([1, 2, 3], polynomial_degree=1,
                    warped_mesh=True,
                    element_family='rt-dg',
                    timestepper_type='SSPRK22',
                    use_ale_moving_mesh=True,
                    no_exports=False, saveplot=True)
