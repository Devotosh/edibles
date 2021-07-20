import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import astropy.constants as cst
from scipy.interpolate import interp1d
from scipy.stats import pearsonr

import inspect
import collections
from math import floor
from lmfit import Model
from lmfit.models import update_param_vals

from edibles.utils.voigt_profile import voigt_absorption_line
from edibles.models import ContinuumModel

from pathlib import Path
from edibles import DATADIR
from edibles import PYTHONDIR

#######################
# Known Issues:
# 1. For now, V_off_next is from the residual of the current fitting. When The major
#    components/component-groups have been addressed, this prediction becomes inaccurate.
# 2. Gamma and f_jj info for Na 3300 doublets are inconsistent, The default for
#    ISLineModel is taken from edibles/data/atomic_lines.txt. And a third version is
#    available at edibles/data/atomic_data.dat.
# 3. Benchmark for the Column Density measurements are required.
#######################
class ISLineFitter():
    def __init__(self, wave, flux, v_resolution=3.0):

        assert len(wave) == len(flux), "Input wave grid and flux must have the same length!"
        self.wave = wave
        self.flux = flux
        self.v_res = v_resolution
        self.wave2fit = wave
        self.flux2fit = flux

        # attribute to archive model-fitting history
        self.model_all = []     # self.model_all[n] has n components in it
        self.result_all = []    # the result class lmfit has lots of info
        self.v_off = []         # a list for V_off from n-component model

        # read in atomic line data frame
        folder = Path(PYTHONDIR+"/data")
        filename = folder / "auxiliary_data" / "line_catalogs" / "edibles_linelist_atoms.csv"
        self.species_df=pd.read_csv(filename)

    def getData2Fit(self, lam_0=None, windowsize=3):
        # clip the data around target wavelength
        # for now windowsize is fixed but can be made to depend on resolution, b, d etc.
        if lam_0 is None:
            lam_0 = self.air_wavelength
        lam_0 = np.asarray(lam_0)
        if len(lam_0.shape) == 0:
            lam_0 = np.asarray([lam_0])

        data_select = np.zeros_like(self.wave)
        for lam in lam_0:
            data_select[(self.wave > lam - windowsize) & (self.wave < lam + windowsize)] = 1

        self.wave2fit = self.wave[data_select == 1]
        self.flux2fit = self.flux[data_select == 1]
        return self.wave2fit, self.flux2fit

    def baysianCriterion(self, known_n_components=None):
        # do the Baysian analysis to determine if self.model_all[-1] is better than self.model_all[-2]
        # if pass: return False to keep adding new components
        # if not pass: return True to exit

        # always continue after the first model (continuum only)
        if len(self.model_all) == 1:
            return False

        # For bench mark purpose, we just assume we know the number of components
        # stop at n+1 component
        if known_n_components is not None:
            if len(self.model_all) >= known_n_components + 2:
                return True
            else:
                return False

    def fit(self, species="KI", n_anchors=5, windowsize=3, known_n_components=2, **kwargs):
        """
        The main fitting method for the class.
        Currently kwargs for select_species_data to make code more pretty

        :param species: name of the species
        :param n_anchors: number of anchor points for spline continuum, default: 5
        :param windowsize: width of wavelength window on EACH side of target line, default: 3 (AA)
        :param kwargs: for select_species_data, allowed kwargs are:
            Wave, OscillatorStrengthm, Gamma and their Max/Min
        :return:
        """

        ########## Step 1, get species info ##########
        spec_name, lam_0, fjj, gamma = self.select_species_data(species=species, **kwargs)
        # debug purpose

        ########## Step 2, get data2fit ##########
        _ = self.getData2Fit(lam_0, windowsize=windowsize)

        ######### Step 3 and 4: build model, fit, repeat ##########
        while True:
            n_components = len(self.model_all)
            print("Fitting model with %i component..." % (n_components))
            model2fit, pars_guess = self.buildModel(lam_0, fjj, gamma, n_anchors)
            result = model2fit.fit(data=self.flux2fit, params=pars_guess, x=self.wave2fit)
            self.__afterFit(model2fit, result)
            if self.baysianCriterion(known_n_components=known_n_components):
                break

        return self.result_all[-2]

    def __afterFit(self, model_new, result_new):
        self.model_all.append(model_new)
        self.result_all.append(result_new)
        self.plotModel(which=-1)

        fitted_pars = result_new.params
        n_components = len(self.model_all) - 1
        self.v_off = []
        for i in range(n_components):
            self.v_off.append(fitted_pars["V_off_Cloud%i" %(i)])

    def buildModel(self, lam_0, fjj, gamma, n_anchors, n_components=None):
        # build continuum and line model then combine them
        # we can reuse continuum model to boost efficiency?

        # Continuum first
        continuum_model = ContinuumModel(n_anchors=n_anchors)
        pars_guess = continuum_model.guess(self.flux2fit, self.wave2fit)
        model2fit = continuum_model

        # check n_components before building line-model
        # only include
        if n_components is None:
            n_components = len(self.model_all)
        if n_components > 0:
            line_model = ISLineModel(n_components,
                                     lam_0=lam_0,
                                     fjj=fjj,
                                     gamma=gamma,
                                     v_res=self.v_res)

            if n_components <= 2:
                V_off_next = self.getNextVoff(lam_0=lam_0)
            else:
                V_off_next = np.average(self.v_off)
            V_off = self.v_off + [V_off_next]
            #V_off = [0.0]*n_components
            pars_guess.update(line_model.guess(V_off=V_off))
            model2fit = model2fit * line_model

        return model2fit, pars_guess

    def plotModel(self, which=-1):
        if which == -1:
            which = len(self.result_all) - 1

        plt.plot(self.wave2fit, self.flux2fit, color="k")
        plt.plot(self.wave2fit, self.result_all[which].best_fit, color="b")

        if which >= 1:
            comps = self.result_all[which].eval_components(x=self.wave2fit)
            plt.plot(self.wave2fit, comps["cont"], color="r")

        plt.xlabel("N Components = {n}".format(n=which))
        plt.show()

    def select_species_data(self, species=None, **kwargs):
    # def select_species_data(self,species=None,Wave=None, WaveMin=None, WaveMax=None,
    #                         OscillatorStrength=None, OscillatorStrengthMin=None, OscillatorStrengthMax=None,
    #                         Gamma=None, GammaMin=None, GammaMax=None):

        '''This method will provide a filtered list of species information that matches
        the specified criteria on sightline/target parameters as well as
        on observational criteria (e.g. wavelength range).

        Use kwargs to make the code look pretty,
        Consider allow both upper and lower cases?
        Allowed kwargs:
        Wave, WaveMin, WaveMax
        OscillatorStrength(Max/Min)
        Gamma(Max/Min)
        '''
        
        # Filtering species
        bool_species_matches = np.zeros(len(self.species_df.index), dtype=bool)
        
        if species is None:
            bool_species_matches = np.ones(len(self.species_df.index), dtype=bool)
        elif (isinstance(species, np.ndarray) | isinstance(species, list)):
            
            for thisobject in species:

                bool_species_matches = (self.species_df.Species.str.contains(thisobject)) | (bool_species_matches)
                
        else:
            
            bool_species_matches = self.species_df.Species == species

        # Filtering Wave
        bool_wave_matches = np.ones(len(self.species_df.index), dtype=bool)
        if "Wave" in kwargs.keys():
            bool_wave_matches = (self.species_df.WavelengthAir == kwargs["Wave"])
        if "WaveMin" in kwargs.keys():
            bool_wave_matches = (self.species_df.WavelengthAir > kwargs["WaveMin"]) & (bool_wave_matches)
        if "WaveMax" in kwargs.keys():
            bool_wave_matches = (self.species_df.WavelengthAir < kwargs["WaveMax"]) & (bool_wave_matches)
            
        # Filtering oscillator strength
        bool_osc_matches = np.ones(len(self.species_df.index), dtype=bool)
        if "OscillatorStrength" in kwargs.keys():
            bool_osc_matches = (self.species_df.OscillatorStrength == kwargs["OscillatorStrength"])
        if "OscillatorStrengthMin" in kwargs.keys():
            bool_osc_matches = (self.species_df.OscillatorStrength > kwargs["OscillatorStrengthMin"]) & (bool_osc_matches)
        if "OscillatorStrengthMax" in kwargs.keys():
            bool_osc_matches = (self.species_df.OscillatorStrength < kwargs["OscillatorStrengthMax"]) & (bool_osc_matches)
            
        # Filtering gamma
        bool_gamma_matches = np.ones(len(self.species_df.index), dtype=bool)
        if "Gamma" in kwargs.keys():
            bool_gamma_matches = (self.species_df.Gamma == kwargs["Gamma"])
        if "GammaMin" in kwargs.keys():
            bool_gamma_matches = (self.species_df.Gamma > kwargs["GammaMin"]) & (bool_gamma_matches)
        if "GammaMax" in kwargs.keys():
            bool_gamma_matches = (self.species_df.Gamma < kwargs["GammaMax"]) & (bool_gamma_matches)
            
            
        # Sum up and output
        ind = np.where(bool_species_matches & bool_wave_matches & bool_osc_matches & bool_gamma_matches)[0]
        self.species_list=self.species_df['Species'].iloc[ind].to_list()
        
        self.air_wavelength=self.species_df['WavelengthAir'].iloc[ind].to_list()
        self.oscillator_strength=self.species_df['OscillatorStrength'].iloc[ind].to_list()
        self.gamma=self.species_df['Gamma'].iloc[ind].to_list()
        return (self.species_list, self.air_wavelength, self.oscillator_strength, self.gamma)

    def determine_vrad_from_correlation(self, wave, flux, model):
        """
        Function to calculate the correlation between an observed spectrum and a model as a function of
        radial velocity and return the radial velocity with the highest correlation coefficient. 
        Args:
            wave (float64): array of wavelengths
            flux (float64): Flux (observed)
            model(float64): model
        Returns:
            vrad_best: radial velocity corresponding to highest correlation. 
        """
        # Create the grid of velocities at which to calculate the correlation. 
        # Using a step size of 0.1 km/s here, and a range of -50 to 50; this should 
        # suffice for most sightlines. 
        v_rad_grid = np.arange(-50.,50.,.1) # in km / s
        all_corr = v_rad_grid * 0.
        #print(v_rad_grid)
        for loop in range(len(v_rad_grid)):
            v_rad = v_rad_grid[loop]
            Doppler_factor = 1. + v_rad / cst.c.to("km/s").value
            #print(Doppler_factor)
            new_wave = wave * Doppler_factor
            
            # Interpolate shifted model to original wavelength grid
            interpolationfunction = interp1d(new_wave, model, kind="cubic", fill_value="extrapolate")
            interpolatedModel = interpolationfunction(wave)
            
            # Calculate correlation coefficient
            this_c, _ = pearsonr(flux, interpolatedModel)
            all_corr[loop] = this_c
            # Return the radial velocity at the maximum correlation.  
        v_rad_best = v_rad_grid[np.argmax(all_corr)]    

        return v_rad_best

    def getNextVoff(self, wave=None, flux=None, lam_0=None):
        if lam_0 is None:
            lam_0 = self.air_wavelength

        if wave is None:
            wave = self.wave2fit

        if flux is None:
            flux = self.flux2fit
            if len(self.result_all) >= 1:
                flux = self.flux2fit - self.result_all[-1].best_fit

        assert len(wave) == len(flux), "Wave grid and flux must be of the same length!"


        linemodel = ISLineModel(1, lam_0=lam_0, fjj=[1]*len(lam_0), gamma=[0]*len(lam_0))
        pars = linemodel.guess(V_off=[0.0])
        #x_model = np.arange(start=self.wave2fit[0], stop=self.wave2fit[-1], step=0.01)
        y_model = linemodel.eval(params=pars, x=wave)

        return self.determine_vrad_from_correlation(self.wave2fit, flux, y_model)



class ISLineModel(Model):
    def __init__(self, n_components,
                 lam_0=[3302.369, 3302.978],
                 fjj=[8.26e-03, 4.06e-03],
                 gamma=[6.280e7, 6.280e7],
                 v_res=3.0,
                 independent_vars=["x"],
                 prefix="",
                 nan_policy="raise",
                 n_step=25,
                 **kwargs):
        """
        :param n_components: int, number of velocity components
        :param lam_0: list, air wavelength of target line. lam_0, fjj and gamma should have same length
        :param fjj: list, oscillator strengths
        :param gamma: list, Gamma parameter related to broadening.
        :param v_res: float, resolution in km/s
        :param independent_vars: from lmfit and Klay's code
        :param prefix: from lmfit and Klay's code
        :param nan_policy: from lmfit and Klay's code
        :param n_step: int, no. of points in 1*FWHM during calculation. Under-sample losses information
        but over-sample losses efficiency.
        :param kwargs: ???
        """
        self.n_components, self.lam_0, self.fjj, self.gamma, self.n_setp = \
            self.__inputCheck(n_components, lam_0, fjj, gamma, n_step)
        self.v_res = v_res

        self.N_init = self.__estimateN(tau0=0.1)

        kwargs.update({"prefix": prefix,
                       "nan_policy": nan_policy,
                       "independent_vars": independent_vars})

        # creat b, N, and V according to no. of components
        self.b_names = ["b_Cloud%i" % (i) for i in range(n_components)]
        self.N_names = ["N_Cloud%i" % (i) for i in range(n_components)]
        self.V_names = ["V_off_Cloud%i" % (i) for i in range(n_components)]
        kwargs["param_names"] = self.b_names + self.N_names + self.V_names
        params = {}
        for name in kwargs["param_names"]:
            if name[0] == "b":
                params[name] = 1.0
            if name[0] == "N":
                params[name] = self.N_init
            if name[0] == "V":
                params[name] = 0.0

        # Other than the default parameters from Cloud0, other parameters are passed in kwargs
        def calcISLineModel(x, b_Cloud0=1.0, N_Cloud0=1.0, V_off_Cloud0=0.0, **kwargs):
            lambda0 = self.lam_0 * self.n_components
            f = self.fjj * self.n_components
            gamma = self.gamma * self.n_components
            v_resolution = self.v_res

            # parse parameters
            bs = [b_Cloud0] * len(self.lam_0)
            Ns = [N_Cloud0] * len(self.lam_0)
            V_offs = [V_off_Cloud0] * len(self.lam_0)

            # just something to print so we know it's working...
            # print("Velocity of Cloud_0:", np.unique(V_offs))

            for name in kwargs.keys():
                if name[0] == "b":
                    bs = bs + [kwargs[name]] * len(self.lam_0)
                if name[0] == "N":
                    Ns = Ns + [kwargs[name]] * len(self.lam_0)
                if name[0] == "V":
                    V_offs = V_offs + [kwargs[name]] * len(self.lam_0)

            # no problem for convolution since we only call voigt_absorption_line once.
            flux = voigt_absorption_line(
                x,
                lambda0=lambda0,
                b=bs,
                N=Ns,
                f=f,
                gamma=gamma,
                v_rad=V_offs,
                v_resolution=v_resolution,
                n_step=self.n_setp
            )

            return flux

        # play with the signature of the calculation function
        # I don't frankly understand what is happening, but it works
        sig = inspect.signature(calcISLineModel)
        base_b = inspect.signature(calcISLineModel).parameters["b_Cloud0"]
        base_N = inspect.signature(calcISLineModel).parameters["N_Cloud0"]
        base_V_off = inspect.signature(calcISLineModel).parameters["V_off_Cloud0"]

        d = {'x': sig.parameters['x']}
        for i in range(n_components):
            b_key = "b_Cloud" + str(i)
            b_val = base_b.replace(name=b_key)
            d[b_key] = b_val

            N_key = "N_Cloud" + str(i)
            N_val = base_N.replace(name=N_key)
            d[N_key] = N_val

            V_off_key = "V_off_Cloud" + str(i)
            V_off_val = base_V_off.replace(name=V_off_key)
            d[V_off_key] = V_off_val

        d = collections.OrderedDict(d)
        calcISLineModel.__signature__ = sig.replace(parameters=tuple(d.values()))

        super().__init__(calcISLineModel, **kwargs)

    def guess(self, V_off=[0.0], **kwargs):
        # For now just type in V_off but we can include v_correlation in the future

        assert len(V_off) == self.n_components, "Number of components do not match."

        pars = self.make_params()
        for i, v in enumerate(V_off):
            pars["%sb_Cloud%i" % (self.prefix, i)].set(value=1.0, min=0, max=10)
            pars["%sN_Cloud%i" % (self.prefix, i)].set(value=self.N_init, min=0)
            pars["%sV_off_Cloud%i" % (self.prefix, i)].set(value=v, min=v-20, max=v+20)
            # we can further constrain min and max on V_off if we have good estimate.

        return update_param_vals(pars, self.prefix, **kwargs)

    def __inputCheck(self, n_components, lam_0, fjj, gamma, n_step):
        # n_components should be int
        if not isinstance(n_components, int):
            raise TypeError("n_components (%.1f) must be an integer!" % n_components)

        # lam_0, fjj, gamma should be LIST have same length
        if not isinstance(lam_0, list) or not isinstance(fjj, list) or not isinstance(gamma, list):
            raise TypeError("lam_0, fjj, gamma should be list")
        len_array = [len(lam_0), len(fjj), len(gamma)]
        if not np.max(len_array) == np.max(len_array):
            raise TypeError("lam_0, fjj, gamma should have the same length")

        # n_step should be int but just in case it's a float
        n_step = floor(n_step)
        return n_components, lam_0, fjj, gamma, n_step

    def __estimateN(self, tau0=1.0):
        lam = self.lam_0[0]
        fjj = self.fjj[0]

        # N = tau * m_e * c^2 / [pi * e^2 * f * lambda(cm) * 1e8]
        N_init = tau0 \
                 * cst.m_e.to("g").value \
                 * (cst.c.to("cm/s").value) ** 2 \
                 / (np.pi * (cst.e.esu.value) ** 2 * fjj * (1e-8 * lam) ** 2 * 1e8)

        return N_init




if __name__ == "__main__":
    from edibles.utils.edibles_oracle import EdiblesOracle
    from edibles.utils.edibles_spectrum import EdiblesSpectrum
    import matplotlib.pyplot as plt
    import time

    # data to fit, around HD183143 Na 3300 doublet, order only
    # v_off at -12 and 3.5
    pythia = EdiblesOracle()
    List = pythia.getFilteredObsList(object=["HD 183143"], OrdersOnly=True, Wave=3302.0)
    filename = List.values.tolist()[1]
    sp = EdiblesSpectrum(filename)
    wave, flux = sp.bary_wave, sp.flux

    # initializing ISLineFitter
    print("="*40)
    print("Testing ISLineFitter, Finger Crossed!")
    test_fitter = ISLineFitter(wave, flux)

    # test line selection
    # WaveMax is passed in **kwarg, if it works, other should work too
    spec_name, lam_0, fjj, gamma = test_fitter.select_species_data(species="NaI", WaveMax=3305)
    print("Fitting: ", test_fitter.species_list)
    print("lam_0: ", test_fitter.air_wavelength)
    print("fjj: ",test_fitter.oscillator_strength)
    print("gamma: ",  test_fitter.gamma)

    # test data clipping
    _ = test_fitter.getData2Fit(windowsize=1.5)
    plt.plot(test_fitter.wave2fit, test_fitter.flux2fit)
    plt.xlabel("Data to Fit")
    plt.show()

    # test guess v_off
    v_off = test_fitter.getNextVoff()
    yrange = [np.min(test_fitter.flux2fit), np.max(test_fitter.flux2fit)]
    lam_0 = np.asarray(test_fitter.air_wavelength) * (1 + v_off / cst.c.to("km/s").value)
    plt.plot(test_fitter.wave2fit, test_fitter.flux2fit)
    for l in lam_0:
        plt.plot([l,l],yrange, color="r")
    plt.xlabel("Next V_off at {v:.2f} km/s".format(v=v_off))
    plt.show()

    # let's do the fitting
    best_result = test_fitter.fit(species="NaI", windowsize=1.5, known_n_components=3, WaveMax=3310)
    print(best_result.fit_report())




