from abc import ABCMeta, abstractmethod
import numpy as np
from scipy.linalg import eig, eigh, solve, lstsq, eigvals
from scipy.sparse.linalg import aslinearoperator, eigs, eigsh, lsqr
from scipy.sparse import diags
from .utils import weighted_norm, parse_backend, IterInv, KernelSquared, _is_real, modified_QR
from warnings import warn

class KoopmanRegression(metaclass=ABCMeta):
    @abstractmethod
    def fit(self, X, Y, backend='auto'):
        """
            For low-rank estimators, compute the matrices U and V.
            For high-rank estimators, pass.
        """
        pass

    def modes(self, observable = lambda x: x):
        """
            Compute the modes associated to the given observable (should be a callable).
        """
        try:
            inv_sqrt_dim = (self.K_X.shape[0])**(-0.5)
            evaluated_observable = observable(self.Y)
            if evaluated_observable.ndim == 1:
                evaluated_observable = evaluated_observable[:,None]
            
            if isinstance(self, LowRankKoopmanRegression):
                evaluated_observable = (self.V.T)@evaluated_observable
            
            self._modes, _, effective_rank, _ = lstsq(self._modes_to_invert, evaluated_observable) 
            self._modes *= inv_sqrt_dim
            return self._modes
        except AttributeError:
            try:
                self.eig()
                return self.modes(observable)
            except AttributeError:
                raise AttributeError("You must first fit the model.")
    
    def forecast(self, X, t=1., which = None):
        try:
            if which is not None:
                evals = self._evals[which][:, None]     # [r,1]
                refuns = self._refuns(X)[:,which]       # [n,r]
                modes = self._modes[which,:]            # [r,n_obs]
            else:
                evals = self._evals[:, None]            # [r,1]
                refuns = self._refuns(X)                # [n,r]
                modes = self._modes                     # [r,n_obs]

            if np.isscalar(t):
                t = np.array([t], dtype=np.float64)[None,:] # [1, t]
            elif np.ndim(t) == 1:
                t = np.array(t, dtype=np.float64)[None,:]   # [1, t]
            else:
                raise ValueError("t must be a scalar or a vector.")
            evals_t = np.power(evals, t) # [r,t]
            forecasted = np.einsum('ro,rt,nr->tno', modes, evals_t, refuns)  # [t,n,n_obs]
            if forecasted.shape[0] <= 1:
                return np.real(forecasted[0])
            else:
                return np.real(forecasted)

        except AttributeError:
            raise AttributeError("You must first fit the model and evaluate the modes with the 'self.modes' method.")

    def spectral_error(self, X = None, Y = None, left = False, axis = None):
        try:
            if X is None or Y is None:
                X = self.X
                Y = self.Y
            if left:
                error = self._lefuns(X) - self._lefuns(Y)@np.diag(self._evals.conj())
            else:
                error = self._refuns(Y) - self._refuns(X)@np.diag(self._evals)
            
            if axis is None:
                return np.linalg.norm(error, ord='fro') / np.sqrt(X.shape[0]*self._evals.shape[0])  
            else:
                return np.linalg.norm(error, axis = axis) / np.sqrt(X.shape[0]*self._evals.shape[0])
        
        except AttributeError:
            raise AttributeError("You must first fit the model and evaluate the modes with the 'self.eig' method.")

    @abstractmethod
    def eig(self):
        """
            Compute the spectral decomposition of the Koopman operator.
        """
        pass
    @abstractmethod
    def predict(self, X):
        """
            Predict the evolution of the state after a single time-step.
        """
        pass

    @abstractmethod
    def risk(self, X = None, Y = None):
        """
            Evaluate the training error (X = Y = None) or the test error.
        """
        pass
    
    def _init_kernels(self, X, Y, backend):
        self.X, self.Y = X.copy(), Y.copy()
        self.backend = parse_backend(backend, X)

        self.K_X = self.kernel(self.X, backend=self.backend)
        self.K_Y = self.kernel(self.Y, backend=self.backend)
        self.K_YX = self.kernel(self.Y, self.X, backend=self.backend)

        if self.backend == 'keops':
            self.K_X = aslinearoperator(self.K_X)
            self.K_Y = aslinearoperator(self.K_Y)
            self.K_YX = aslinearoperator(self.K_YX)

        self.dtype = self.K_X.dtype
        
    def _init_risk(self, X, Y):
        if (X is not None) and (Y is not None):
            K_yY = self.kernel(Y, self.Y, backend = self.backend)
            K_Xx = self.kernel(self.X, X, backend = self.backend)
            if self.backend == 'keops':
                K_yY = aslinearoperator(K_yY)
                K_Xx = aslinearoperator(K_Xx)
            _Y = Y
        else:
            K_yY = self.K_Y
            K_Xx = self.K_X
            _Y = self.Y
        r_yy = 0
        for y in _Y:
            y = y[None,:]
            r_yy += self.kernel(y,y, backend='cpu')
        r_yy = np.squeeze(r_yy)*((_Y.shape[0])**(-1))
             
        return K_yY, K_Xx, r_yy

class KernelRidgeRegression(KoopmanRegression):
    def __init__(self, kernel, tikhonov_reg = None):
        self.tikhonov_reg = tikhonov_reg
        self.kernel = kernel

    def fit(self, X, Y, backend = 'cpu'):        
        if backend != 'cpu':
            warn("Keops backend not implemented for KernelRidgeRegression. Use TruncatedKernelRidgeRegression instead. Forcing 'cpu' backend. ")
        self._init_kernels(X, Y, 'cpu')
    
    def eigvals(self, k=None):
        """Eigenvalues of the Koopman operator

        Returns:
            evals: Eigenvalues of the Koopman operator. If k != None, return the k largest
        """
        try:
            dim = self.K_X.shape[0]
            dim_inv = dim**(-1)
            K_reg = self.K_X
            if self.tikhonov_reg is not None:
                tikhonov = np.eye(dim, dtype=self.dtype)*(self.tikhonov_reg*dim)
                K_reg += tikhonov
            
            if k is None:
                evals = eigvals(self.K_YX, K_reg)
            else:
                evals = eigs(self.K_YX, k, K_reg, return_eigenvectors=False)   
            return evals

        except AttributeError:
            raise AttributeError("You must first fit the model.")
        
    def eig(self):
        """Eigenvalue decomposition of the Koopman operator

        Returns:
            evals: Eigenvalues of the Koopman operator
            levecs: Matrix whose columns are the weigths of left eigenfunctions of the Koopman operator
            revecs: Matrix whose columns are  the weigths of right eigenfunctions of the Koopman operator
        """
        try:
            dim = self.K_X.shape[0]
            dim_inv = dim**(-1)
            sqrt_inv_dim = dim_inv**(0.5)
            if self.tikhonov_reg is not None:
                tikhonov = np.eye(dim, dtype=self.dtype)*(self.tikhonov_reg*dim)
                K_reg = self.K_X + tikhonov    
                self._evals, self._levecs, self._revecs = eig(self.K_YX, K_reg, left=True, right=True)
            else:
                self._evals, self._levecs, self._revecs = eig(np.linalg.pinv(self.K_X)@self.K_YX, left=True, right=True)
            
            idx_ = np.argsort(np.abs(self._evals))[::-1]
            self._evals = self._evals[idx_]
            self._levecs, self._revecs = self._levecs[:,idx_], self._revecs[:,idx_]
            
            norm_r = weighted_norm(self._revecs,self.K_X)*dim_inv
            norm_l = weighted_norm(self._levecs,self.K_Y)*dim_inv

            self._revecs = self._revecs @ np.diag(norm_r**(-0.5))
            self._levecs = self._levecs @ np.diag(norm_l**(-0.5))
            self._modes_to_invert = self.K_YX@self._revecs * dim_inv

            self._refuns = lambda X:  sqrt_inv_dim*self.kernel(X, self.X, backend=self.backend)@self._revecs
            self._lefuns = lambda X:  sqrt_inv_dim*self.kernel(X, self.Y, backend=self.backend)@self._levecs

            return self._evals, self._lefuns, self._refuns

        except AttributeError:
            raise AttributeError("You must first fit the model.")

    def predict(self, X):
        try:
            dim = self.X.shape[0]
            if self.tikhonov_reg is not None:
                tikhonov = np.eye(dim, dtype=self.dtype)*(self.tikhonov_reg*dim)
                _Z = solve(self.K_X + tikhonov,self.Y, assume_a='pos')      
            else:
                _Z, _, _, _ = lstsq(self.K_X)@self.Y
            if X.ndim == 1:
                X = X[None,:]
            _S = self.kernel(X, self.X, backend = self.backend)
            return _S@_Z
        except AttributeError:
            raise AttributeError("You must first fit the model.")
    
    def risk(self, X = None, Y = None):
        try:
            K_yY, K_Xx, r = self._init_risk(X, Y)
            val_dim, dim = K_yY.shape
            if self.tikhonov_reg is not None:
                tikhonov = np.eye(dim, dtype=self.dtype)*(self.tikhonov_reg*dim)
                C = solve(self.K_X + tikhonov, K_Xx, assume_a='pos')
            else:
                C, _, _, _ = lstsq(self.K_X, K_Xx)

            r -= 2*(val_dim**(-1))*np.trace(K_yY@C)
            r += (val_dim**(-1))*np.trace(C.T@(self.K_Y@C))
            return r
        except AttributeError:
                raise AttributeError("You must first fit the model.")

class TruncatedKernelRidgeRegression(KoopmanRegression):
    def __init__(self, kernel, rank, tikhonov_reg):
        """
            This class implements a subset of the functionalities of KernelRidgeRegression and should be used only for very large datasets. Notable differences with KernelRidgeRegression are:
            1. tikhonov_reg must be a scalar, and cannot be None (i.e. no regularization)
            2. the eig method only computes the right eigenfunctions.
            3. The risk method is not implemented.
        """
        self.rank = rank
        assert np.isscalar(tikhonov_reg), f"Numerical value expected, got {tikhonov_reg} instead."
        self.tikhonov_reg = tikhonov_reg
        self.kernel = kernel

    def fit(self, X, Y, backend = 'auto'):    
        if backend != 'keops':
            warn("CPU backend not implemented for TruncatedKernelRidgeRegression. Use KernelRidgeRegression instead. Forcing 'keops' backend. ")
        self._init_kernels(X, Y, 'keops')
        
    def eig(self):
        """Eigenvalue decomposition of the Koopman operator

        Returns:
            evals: Eigenvalues of the Koopman operator
            revecs: Matrix whose columns are  the weigths of right eigenfunctions of the Koopman operator
        """
        warn("Left eigenfunctions are not evaluated in TruncatedKernelRidgeRegression. If needed, use KernelRidgeRegression instead.")
        try:
            dim = self.K_X.shape[0]
            dim_inv = dim**(-1)
            sqrt_inv_dim = dim_inv**(0.5)
            
            tikhonov = aslinearoperator(diags(np.ones(dim, dtype=self.dtype)*self.tikhonov_reg*dim))
            Minv = IterInv(self.kernel, self.X, self.tikhonov_reg*dim)
            self._evals, self._revecs = eigs(self.K_YX, self.rank, self.K_X + tikhonov,  Minv=Minv)
            idx_ = np.argsort(np.abs(self._evals))[::-1]
            self._evals = self._evals[idx_]
            self._revecs = np.asfortranarray(self._revecs[:,idx_])
            
            norm_r = weighted_norm(self._revecs,self.K_X)*dim_inv
            self._revecs = self._revecs @ np.diag(norm_r**(-0.5))

            self._modes_to_invert = self.K_YX.matmat(np.asfortranarray(self._revecs))* dim_inv

            if self.backend == 'keops':
                self._refuns = lambda X: sqrt_inv_dim*self.K_X.matmat(np.asfortranarray(self._revecs))

            return self._evals, self._refuns

        except AttributeError:
            raise AttributeError("You must first fit the model.")

    def predict(self, X):
        try:
            dim = self.X.shape[0]
            _Z = IterInv(self.kernel,X,self.tikhonov_reg*dim).matmat(self.Y)           
            if X.ndim == 1:
                X = X[None,:]
            _S = self.kernel(X, self.X, backend = self.backend)
            return _S@_Z
        except AttributeError:
            raise AttributeError("You must first fit the model.")
 
    def risk(self, X = None, Y = None):
        raise NotImplementedError("Risk evaluation not implemented for TruncatedKernelRidgeRegression")

class LowRankKoopmanRegression(KoopmanRegression):   
    def eigvals(self, k=None):
        """Eigenvalues of the Koopman operator

        Returns:
            evals: Eigenvalues of the Koopman operator. Parameter k not used in low-rank estimators.
        """
        try:
            dim_inv = (self.K_X.shape[0])**(-1)
            sqrt_inv_dim = dim_inv**0.5
            if self.backend == 'keops':
                C = dim_inv* self.K_YX.matmat(np.asfortranarray(self.U)) 
            else:
                C = dim_inv* self.K_YX@self.U 
            
            evals =  eigvals(self.V.T@C)         
            return evals

        except AttributeError:
            raise AttributeError("You must first fit the model.")
            
    def eig(self):
        """Eigenvalue decomposition of the Koopman operator

        Returns:
            evals: Eigenvalues of the Koopman operator
            levecs: Matrix whose columns are the weigths of left eigenfunctions of the Koopman operator
            revecs: Matrix whose columns are  the weigths of right eigenfunctions of the Koopman operator
        """
        try:
            dim_inv = (self.K_X.shape[0])**(-1)
            sqrt_inv_dim = dim_inv**0.5
            if self.backend == 'keops':
                C = dim_inv* self.K_YX.matmat(np.asfortranarray(self.U)) 
            else:
                C = dim_inv* self.K_YX@self.U 
            
            vals, lv, rv =  eig(self.V.T@C, left=True, right=True)
            self._evals = vals

            self._levecs = self.V@lv
            self._revecs = self.U@rv

            # sort the evals w.r.t. modulus 
            idx_ = np.argsort(np.abs(self._evals))[::-1]
            self._evals = self._evals[idx_]
            self._levecs, self._revecs = self._levecs[:,idx_], self._revecs[:,idx_]
            rv = rv[:,idx_]
            
            norm_r = weighted_norm(self._revecs,self.K_X)*dim_inv
            norm_l = weighted_norm(self._levecs,self.K_Y)*dim_inv

            self._revecs = self._revecs @ np.diag(norm_r**(-0.5))
            self._levecs = self._levecs @ np.diag(norm_l**(-0.5))

            self._modes_to_invert = rv @np.diag(self._evals*(norm_r**(-0.5)))

            if self.backend == 'keops':
                self._levecs = np.asfortranarray(self._levecs)
                self._revecs = np.asfortranarray(self._revecs)
                self._modes_to_invert = np.asfortranarray(self._modes_to_invert)
                self._refuns = lambda X: sqrt_inv_dim*aslinearoperator(self.kernel(X, self.X, backend=self.backend)).matmat(self._revecs)
                self._lefuns = lambda X: sqrt_inv_dim*aslinearoperator(self.kernel(X, self.Y, backend=self.backend)).matmat(self._levecs)
            else:
                self._refuns = lambda X:  sqrt_inv_dim*self.kernel(X, self.X, backend=self.backend)@self._revecs
                self._lefuns = lambda X:  sqrt_inv_dim*self.kernel(X, self.Y, backend=self.backend)@self._levecs

            return self._evals, self._lefuns, self._refuns
        except AttributeError:
                raise AttributeError("You must first fit the model.")

    def predict(self, X):
        try:
            sqrt_dim_inv = (self.K_X.shape[0])**(-0.5)
            _Z = sqrt_dim_inv * self.V.T @ self.Y
            if X.ndim == 1:
                X = X[None,:]
            _init_K = self.kernel(X, self.X, backend = self.backend)
            if self.backend == 'keops':
                _S = sqrt_dim_inv * (aslinearoperator(_init_K).matmat(np.asfortranarray(self.U)))
            else:
                _S = sqrt_dim_inv * _init_K@self.U
            return _S@_Z 
        except AttributeError:
                raise AttributeError("You must first fit the model.")
    
    def risk(self, X = None, Y = None):
        try:
            K_yY, K_Xx, r = self._init_risk(X, Y)
            val_dim, dim = K_yY.shape
            sqrt_inv_dim = dim**(-0.5)
            V = sqrt_inv_dim*self.V
            U = sqrt_inv_dim*self.U
            if self.backend == 'keops':
                C = K_yY.matmat(np.asfortranarray(V))
                D = ((K_Xx.T).matmat(np.asfortranarray(U))).T
                E = (V.T)@self.K_Y.matmat(np.asfortranarray(V))
            else:
                C = K_yY@V
                D = (K_Xx.T@U).T
                E = (V.T)@self.K_Y@V
            r -= 2*(val_dim**(-1))*np.trace(C@D)
            r += (val_dim**(-1))*np.trace(D.T@E@D)
            return r
        except AttributeError:
                raise AttributeError("You must first fit the model.")

class ReducedRankRegression(LowRankKoopmanRegression):
    def __init__(self, kernel, rank, tikhonov_reg = None):
        self.rank = rank
        self.tikhonov_reg = tikhonov_reg
        self.kernel = kernel

    def fit(self, X, Y, backend = 'auto'):
        self._init_kernels(X, Y, backend)
        dim = self.K_X.shape[0]
        inv_dim = dim**(-1)

        if self.tikhonov_reg is not None:
            alpha =  self.tikhonov_reg*dim 
            K = inv_dim*(self.K_Y@self.K_X)
            if self.backend == 'keops':
                tikhonov = aslinearoperator(diags(np.ones(dim, dtype=self.dtype)*alpha))
                Minv = IterInv(self.kernel, self.X, alpha)
                sigma_sq, U = eigs(K, self.rank, self.K_X + tikhonov,  Minv=Minv)                
            else:
                tikhonov = np.eye(dim, dtype=self.dtype)*alpha   
                sigma_sq, U = eig(K, self.K_X + tikhonov)

            if not _is_real(sigma_sq):
                _max_imag_part = np.max(np.abs(sigma_sq.imag))
                warn(f"The computed eigenvalues should be real, but have imaginary parts as high as {_max_imag_part:.2e}. Discarting imaginary parts.")
            sigma_sq = np.real(sigma_sq)
            sort_perm = np.argsort(sigma_sq)[::-1]
            sigma_sq = sigma_sq[sort_perm][:self.rank]
            U = U[:,sort_perm][:,:self.rank]
            #Check that the eigenvectors are real (or have a global phase at most)
            if not _is_real(U):
                warn("Computed projector is not real or a global complex phase is present. The Kernel matrix is either severely ill conditioned or non-symmetric, discarting imaginary parts.")

            U = np.real(U) 
            _M = KernelSquared(self.kernel, self.X, inv_dim, self.tikhonov_reg, self.backend)
            _nrm_sq = weighted_norm(U, M = _M )

            if any(_nrm_sq < _nrm_sq.max() * 4.84e-32):  
                U, perm = modified_QR(U, M = _M, pivoting=True, numerical_rank=False)
                U = U[:,np.argsort(perm)]         
                warn(f"Chosen rank is to high. Reducing rank {self.rank} -> {U.shape[1]}.")
                self.rank = U.shape[1]
            else:
                U = U@np.diag(1/_nrm_sq**(0.5))                        
            V = (self.K_X@np.asfortranarray(U))  

        else:
            if self.backend == 'keops':
                sigma_sq, V = eigsh(self.K_Y, self.rank)
                V = V@np.diag(np.sqrt(dim)/(np.linalg.norm(V,ord=2,axis=0)))
                U = lsqr(self.K_X, V)
            else:
                sigma_sq, V = eigh(self.K_Y)
                sort_perm = np.argsort(sigma_sq)[::-1]
                sigma_sq = sigma_sq[sort_perm][:self.rank]
                V = V[:,sort_perm][:,:self.rank]
                V = V@np.diag(np.sqrt(dim)/(np.linalg.norm(V,ord=2,axis=0)))
                U, _, effective_rank, _ = lstsq(self.K_X, V)
        self.V = V 
        self.U = U

class PrincipalComponentRegression(LowRankKoopmanRegression):
    def __init__(self, kernel, rank):
        self.rank = rank
        self.kernel = kernel

    def fit(self, X, Y, backend = 'auto'):
        self._init_kernels(X, Y, backend)
        dim = self.K_X.shape[0]
        K = self.K_X

        if self.backend == 'keops':
            S, V = eigsh(K, self.rank)
            sigma_sq = S**2
            sort_perm = np.argsort(sigma_sq)[::-1]
            sigma_sq = sigma_sq[sort_perm]
            V = V[:,sort_perm]
            S = S[sort_perm]
        else:
            S, V = eigh(K)
            sigma_sq = S**2
            sort_perm = np.argsort(sigma_sq)[::-1]
            sigma_sq = sigma_sq[sort_perm]
            S = S[::-1][:self.rank]
            V = V[:,::-1][:,:self.rank]
        
        _test = S>2.2e-16
        if all(_test):            
            self.V = V * np.sqrt(dim) 
            self.U = V@np.diag(S**-1) * np.sqrt(dim)
        else:
            self.V = V[:_test] * np.sqrt(dim) 
            self.U = V[:_test]@np.diag(S[:_test]**-1) * np.sqrt(dim)
            warn(f"Chosen rank is to high. Reducing rank {self.rank} -> {self.V.shape[1]}.")
            self.rank = self.V.shape[1]