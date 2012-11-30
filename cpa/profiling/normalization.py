import abc
class BaseNormalization(object):
    __metaclass__ = abc.ABCMeta
    
    _cached_colmask = None

    def __init__(self, cache, param_dir):
        self.cache = cache
        self.dir = os.path.join(cache.cache_dir, param_dir)
        self._colmask_filename = os.path.join(self.dir, 'colmask.npy')

    def _params_filename(self, plate):
        return os.path.join(self.dir, 'params', 
                            unicode(plate) + '.npy')

    @property
    def _colmask(self):
        if self._cached_colmask is None:
            self._cached_colmask = np_load(self._colmask_filename)
        return self._cached_colmask

    @abc.abstractmethod
    def normalize(self, plate, data):
        pass
        
    @property
    def colnames(self):
        """Return the names of the columns returned by normalize()"""
        return [col
                for col, keep in zip(self.cache.colnames, self._colmask) 
                if keep]

    #
    # Methods to precompute the normalizations
    #

    def _create_cache(self, predicate, resume=False):
        self._create_cache_params(predicate, resume)
        self._create_cache_colmask(predicate)

    def _get_controls(self, predicate):
        """Return a dictionary mapping plate names to lists of control wells"""
        plates_and_images = {}
        for row in cpa.db.execute("select distinct %s, %s from %s where %s"%
                                  (cpa.properties.plate_id, 
                                   ', '.join(cpa.dbconnect.image_key_columns()),
                                   cpa.properties.image_table, predicate)):
            plate = row[0]
            imKey = tuple(row[1:])
            plates_and_images.setdefault(plate, []).append(imKey)
        return plates_and_images

    def _create_cache_colmask(self, predicate):
        colmask = None
        for plate, imKeys in self._get_controls(predicate).items():
            params = np_load(self._params_filename(plate))
            if len(params) == 0:
                continue # No DMSO wells, so no params
            nonzero = self._check_param_zero(params)
            if colmask is None:
                colmask = nonzero
            else:
                colmask &= nonzero
        np.save(self._colmask_filename, colmask)

    
    @abc.abstractmethod
    @staticmethod
    def _compute_params(features):
        pass

    @abc.abstractproperty
    def _null_param(self):
        """
        Return a value that corresponds to a null value for the normalization
        parameter.
        """
        pass
        
    @abc.abstractproperty
    def _check_param_zero(self, params):
        """
        Return a boolean vector of length = len(colmask), where an element is 
        True iff the normalization for the corresponding variable can be 
        calculated. E.g. scale == 0.
        """
        pass
        
    def _create_cache_params_1(self, plate, imKeys, filename):
        features = self.cache.load(imKeys)[0]
        if len(features) == 0:
            logger.warning('No DMSO features for plate %s' % str(plate))
            params = self._null_param
        else:
            params = self._compute_params(features)
        np.save(filename, params)

    def _create_cache_params(self, predicate, resume=False):
        controls = self._get_controls(predicate)
        for i, (plate, imKeys) in enumerate(make_progress_bar('Params')(controls.items())):
            filename = self._params_filename(plate)
            if i == 0:
                _check_directory(os.path.dirname(filename), resume)
            if resume and os.path.exists(filename):
                continue
            self._create_cache_params_1(plate, imKeys, filename)
            
            
            
class RobustStdNormalization_derived(BaseNormalization):
    def __init__(self, cache, param_dir='robust_std'):
        super(RobustStdNormalization_derived, self).__init__(cache, param_dir)

    def normalize(self, plate, data):
        params = np_load(self._params_filename(plate))
        assert data.shape[1] == params.shape[1]
        data = data[:, self._colmask]
        params = params[:, self._colmask]
        shift = params[0]
        scale = params[1]
        assert np.all(scale > 0)
        return (data - shift) / scale

    def _compute_params(features):
        m = features.shape[1]
        params = np.ones((2, m)) * np.nan
        c = Gaussian.ppf(3/4.)
        for j in xrange(m):
            d = np.median(features[:, j])
            params[0, j] = d
            params[1, j] = np.median(np.fabs(features[:, j] - d) / c)
        return params                   

    def _null_param(self):
        return np.zeros((0, len(self.cache.colnames)))
        
    def _check_param_zero(self, params):
        return params[1] == 0    


class RobustLinearNormalization_derived(BaseNormalization):
    def __init__(self, cache, param_dir='robust_linear'):
        super(RobustStdNormalization_derived, self).__init__(cache, param_dir)
        
    def normalize(self, plate, data):
        percentiles = np_load(self._params_filename(plate))
        assert data.shape[1] == percentiles.shape[1]
        data = data[:, self._colmask]
        percentiles = percentiles[:, self._colmask]
        divisor = (percentiles[1] - percentiles[0])
        assert np.all(divisor > 0)
        return (data - percentiles[0]) / divisor
        
    def _compute_params(features):
        m = features.shape[1]
        percentiles = np.ones((2, m)) * np.nan
        for j in xrange(m):
            percentiles[0, j] = scoreatpercentile(features[:, j], 1)
            percentiles[1, j] = scoreatpercentile(features[:, j], 99)
        return percentiles

    def _null_param(self):
        return np.zeros((0, len(self.cache.colnames)))
        
    def _check_param_zero(self, params):
        return params[1] == 0        
        
                        
class RobustStdNormalization(object):
    _cached_colmask = None

    def __init__(self, cache, param_dir='robust_std'):
        self.cache = cache
        self.dir = os.path.join(cache.cache_dir, param_dir)
        self._colmask_filename = os.path.join(self.dir, 'colmask.npy')

    def _params_filename(self, plate):
        return os.path.join(self.dir, 'params', 
                            unicode(plate) + '.npy')

    @property
    def _colmask(self):
        if self._cached_colmask is None:
            self._cached_colmask = np_load(self._colmask_filename)
        return self._cached_colmask

    def normalize(self, plate, data):
        """
        Normalize the data according to the precomputed normalization
        for the specified plate. The normalized data may have fewer
        columns that the unnormalized data, as columns may be removed
        if normalizing them is impossible.

        """
        params = np_load(self._params_filename(plate))
        assert data.shape[1] == params.shape[1]
        data = data[:, self._colmask]
        params = params[:, self._colmask]
        shift = params[0]
        scale = params[1]
        assert np.all(scale > 0)
        return (data - shift) / scale

    @property
    def colnames(self):
        """Return the names of the columns returned by normalize()"""
        return [col
                for col, keep in zip(self.cache.colnames, self._colmask) 
                if keep]

    #
    # Methods to precompute the normalizations
    #

    def _create_cache(self, predicate, resume=False):
        self._create_cache_params(predicate, resume)
        self._create_cache_colmask(predicate)

    def _get_controls(self, predicate):
        """Return a dictionary mapping plate names to lists of control wells"""
        plates_and_images = {}
        for row in cpa.db.execute("select distinct %s, %s from %s where %s"%
                                  (cpa.properties.plate_id, 
                                   ', '.join(cpa.dbconnect.image_key_columns()),
                                   cpa.properties.image_table, predicate)):
            plate = row[0]
            imKey = tuple(row[1:])
            plates_and_images.setdefault(plate, []).append(imKey)
        return plates_and_images

    def _create_cache_colmask(self, predicate):
        colmask = None
        for plate, imKeys in self._get_controls(predicate).items():
            params = np_load(self._params_filename(plate))
            if len(params) == 0:
                continue # No DMSO wells, so no params
            nonzero = self._check_param_zero(params)
            if colmask is None:
                colmask = nonzero
            else:
                colmask &= nonzero
        np.save(self._colmask_filename, colmask)

    @staticmethod
    def _compute_params(features):
        m = features.shape[1]
        params = np.ones((2, m)) * np.nan
        c = Gaussian.ppf(3/4.)
        for j in xrange(m):
            d = np.median(features[:, j])
            params[0, j] = d
            params[1, j] = np.median(np.fabs(features[:, j] - d) / c)
        return params

    @property
    def _null_param(self):
        return np.zeros((0, len(self.cache.colnames)))
        
    def _check_param_zero(self, params):
        return params[1] == 0
        
    def _create_cache_params_1(self, plate, imKeys, filename):
        features = self.cache.load(imKeys)[0]
        if len(features) == 0:
            logger.warning('No DMSO features for plate %s' % str(plate))
            params = self._null_param
        else:
            params = self._compute_params(features)
        np.save(filename, params)

    def _create_cache_params(self, predicate, resume=False):
        controls = self._get_controls(predicate)
        for i, (plate, imKeys) in enumerate(make_progress_bar('Params')(controls.items())):
            filename = self._params_filename(plate)
            if i == 0:
                _check_directory(os.path.dirname(filename), resume)
            if resume and os.path.exists(filename):
                continue
            self._create_cache_params_1(plate, imKeys, filename)
