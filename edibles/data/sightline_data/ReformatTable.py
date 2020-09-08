"""
small routine to reformat the multi-column sightline info to multi-row sightline info
"""

"""
remark: can we rename the target id to 'target' instead of 'object'. object is a thing in python...
But object is also a thing in SIMBAD. 

We will also set prefereed values here. 
For E(B-V): preferred = E(B-V)_Simbad

"""

import numpy as np
import glob
import pandas as pd

EBV_preferred = 'E(B-V)_Simbad'

inputs=glob.glob('Input*.csv')

ref=0
references=[]
references_counter=[]
ref_counter=1

for i in inputs:

    print(i)

    data = np.genfromtxt(i, dtype=None, delimiter=",", names=True)

    colnames=data.dtype.names

    nr_rows = data.shape[0]

    object_id=[]
    value=[]
    unc_lower=[]
    unc_upper=[]
    reference_id=[]
    preferred_flag=[]

    for row in np.arange(nr_rows):

        nr_cols = len(data[row])

        for col in np.arange(1,nr_cols):

           if col != 'False':

               object_id.append(data[row][0].astype(str))

               value.append(data[row][col])
               value.append(data[row][col].astype(str))
               unc_lower.append(np.nan)
               unc_upper.append(np.nan)

               reference_id.append(col+ref)
               preferred_flag.append(1)# not setting the PREFERED VALUE flag yet.

    parameter = i[5:-4]

    df=pd.DataFrame(list(zip(object_id,value,unc_lower,unc_upper,reference_id,preferred_flag)), columns=["object","value","unc_lower","unc_upper","reference_id","preferred_flag"])

    df.to_csv('Targets_'+parameter+'.csv', index=False, na_rep='NaN')

    for c in np.arange(1,nr_cols):
        references_counter.append(ref_counter)
        references.append(colnames[c])
        ref_counter=ref_counter+1
        print(ref_counter)

    ref=ref+nr_cols-1

dfr=pd.DataFrame(list(zip(references_counter, references)), columns=["reference_id","source"])
dfr.to_csv('References.csv', index=False, na_rep='NaN')